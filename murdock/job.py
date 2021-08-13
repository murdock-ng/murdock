import asyncio
import os
import secrets
import shutil
import signal
import time

from typing import Optional
from asyncio.subprocess import Process

from bson.objectid import ObjectId
from pydantic import BaseModel

from murdock.config import (
    GITHUB_REPO, MURDOCK_BASE_URL, MURDOCK_ROOT_DIR, MURDOCK_SCRIPTS_DIR,
    MURDOCK_USE_JOB_TOKEN, CI_FASTTRACK_LABELS
)
from murdock.log import LOGGER


class PullRequestInfo(BaseModel):
    title: str
    number: int
    merge_commit: str
    branch: str
    commit: str
    user: str
    url: str
    base_repo: str
    base_branch: str
    base_commit: str
    base_full_name: str
    mergeable: bool
    labels: list[str]


class FinishedJobModel(BaseModel):
    id: str
    since: float
    result: str
    output_url: str
    runtime: float
    status: dict
    prinfo: PullRequestInfo


class QueuedJobModel(BaseModel):
    prinfo: PullRequestInfo
    since: float
    fasttracked: bool


class RunningJobModel(BaseModel):
    prinfo: PullRequestInfo
    since: float
    status: dict


class MurdockJob:

    def __init__(self, pr: PullRequestInfo):
        self.result : Optional[str] = None
        self.proc : Optional[Process] = None
        self.output : str = ""
        self.pr : PullRequestInfo = pr
        self.start_time : float = time.time()
        self.stop_time  : float = 0
        self.canceled : bool = False
        self.status : dict = { "status": "" }
        self.fasttracked : bool = any(
            label in CI_FASTTRACK_LABELS for label in pr.labels
        )
        self.token : str = secrets.token_urlsafe(32)
        work_dir_relative : str = os.path.join(
            GITHUB_REPO,
            str(self.pr.number),
            self.pr.commit,
            str(self.start_time)
        )
        self.work_dir : str = os.path.join(MURDOCK_ROOT_DIR, work_dir_relative)
        self.http_dir : str = os.path.join(MURDOCK_BASE_URL, work_dir_relative)
        self.output_url : str = os.path.join(self.http_dir, "output.html")

    @staticmethod
    def create_dir(work_dir: str):
        try:
            LOGGER.debug(f"Creating directory '{work_dir}'")
            os.makedirs(work_dir)
        except FileExistsError:
            LOGGER.debug(f"Directory '{work_dir}' already exists, recreate")
            shutil.rmtree(work_dir)
            os.makedirs(work_dir)

    @staticmethod
    def remove_dir(work_dir):
        LOGGER.info(f"Removing directory '{work_dir}'")
        try:
            shutil.rmtree(work_dir)
        except FileNotFoundError:
            LOGGER.debug(
                f"Directory '{work_dir}' doesn't exist, cannot remove"
            )

    @property
    def runtime(self) -> float:
        return self.stop_time - self.start_time

    @property
    def runtime_human(self) -> str:
        if self.runtime > 86400:
            runtime_format = "%Dd:%Hh:%Mm:%Ss"
        elif self.runtime > 3600:
            runtime_format = "%Hh:%Mm:%Ss"
        elif self.runtime > 60:
            runtime_format = "%Mm:%Ss"
        else:
            runtime_format = "%Ss"
        return time.strftime(runtime_format, time.gmtime(self.runtime))

    def queued_model(self):
        return QueuedJobModel(
            prinfo=self.pr,
            since=self.start_time,
            fasttracked=self.fasttracked
        ).dict()

    def running_model(self):
        return RunningJobModel(
            prinfo=self.pr,
            since=self.start_time,
            status=self.status
        ).dict()

    @staticmethod
    def to_db_entry(job):
        return {
            "since" : job.start_time,
            "runtime": job.runtime,
            "result": job.result,
            "output_url": job.output_url,
            "work_dir": job.work_dir,
            "status": job.status,
            "prinfo": job.pr.dict(),
        }

    @staticmethod
    def from_db_entry(entry: dict):
        return FinishedJobModel(
            id=str(entry["_id"]), **entry
        ).dict()

    @property
    def env(self):
        _env = { 
            "CI_PULL_COMMIT" : self.pr.commit,
            "CI_PULL_REPO" : GITHUB_REPO,
            "CI_PULL_BRANCH" : self.pr.branch,
            "CI_PULL_NR" : str(self.pr.number),
            "CI_PULL_URL" : self.pr.url,
            "CI_PULL_TITLE" : self.pr.title,
            "CI_PULL_USER" : self.pr.user,
            "CI_BASE_REPO" : self.pr.base_repo,
            "CI_BASE_BRANCH" : self.pr.base_branch,
            "CI_BASE_COMMIT" : self.pr.base_commit,
            "CI_SCRIPTS_DIR" : MURDOCK_SCRIPTS_DIR,
            "CI_PULL_LABELS" : ";".join(self.pr.labels),
            "CI_BUILD_HTTP_ROOT" : self.http_dir,
            "CI_BASE_URL": MURDOCK_BASE_URL,
        }

        if MURDOCK_USE_JOB_TOKEN:
            _env.update({
                "CI_API_TOKEN": self.token,
            })

        if self.pr.mergeable:
            _env.update({"CI_MERGE_COMMIT": self.pr.merge_commit})

        return _env

    def __repr__(self) -> str:
        return (
            f"sha:{self.pr.commit[0:7]} (PR #{self.pr.number})"
        )

    def __eq__(self, other) -> bool:
        return other is not None and self.pr.commit == other.pr.commit

    async def execute(self):
        MurdockJob.create_dir(self.work_dir)
        LOGGER.debug(f"Launching build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(MURDOCK_SCRIPTS_DIR, "build.sh"), "build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, _ = await self.proc.communicate()
        self.output = out.decode()
        if self.proc.returncode == 0:
            self.result = "passed"
        elif self.proc.returncode in [
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            self.result = "stopped"
        else:
            self.result = "errored"
        LOGGER.debug(
            f"Job {self} {self.result} (ret: {self.proc.returncode})"
        )

        # If the job was stopeed, just return now and skip the post_build action
        if self.result == "stopped":
            LOGGER.debug(f"Job {self} stopped before post_build action")
            self.proc = None
            return

        # Store build output in text file
        try:
            with open(os.path.join(
                self.work_dir, "output.txt"), "w"
            ) as output:
                output.write(self.output)
        except Exception as exc:
            LOGGER.warning(
                f"Job error for {self}: cannot write output.txt: {exc}"
            )

        # Remove build subdirectory
        MurdockJob.remove_dir(os.path.join(self.work_dir, "build"))

        LOGGER.debug(f"Launch post_build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(MURDOCK_SCRIPTS_DIR, "build.sh"), "post_build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, err = await self.proc.communicate()
        if self.proc.returncode not in [
            0,
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            LOGGER.warning(
                f"Job error for {self}: Post build action failed:\n"
                f"out: {out.decode()}"
                f"err: {err.decode()}"
            )
        self.proc = None

    async def stop(self):
        LOGGER.debug(f"Job {self} immediate stop requested")
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGKILL]:
            if self.proc is not None and self.proc.returncode is None:
                LOGGER.debug(f"Send signal {sig} to job {self}")
                self.proc.send_signal(sig)
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    LOGGER.debug(f"Couldn't stop job {self} with {sig}")
        LOGGER.debug(f"Removing job working directory '{self.work_dir}'")
        MurdockJob.remove_dir(self.work_dir)
