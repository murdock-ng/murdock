import asyncio
import json
import os
import secrets
import shutil
import signal
import time
import uuid

from typing import Optional
from asyncio.subprocess import Process

from murdock.config import GLOBAL_CONFIG, CI_CONFIG, GITHUB_CONFIG
from murdock.log import LOGGER
from murdock.models import (
    PullRequestInfo, CommitModel, JobModel, FinishedJobModel
)
from murdock.config import MurdockSettings


class MurdockJob:

    def __init__(
        self, commit: CommitModel,
        ref: Optional[str] = None,
        pr: Optional[PullRequestInfo] = None,
        config: Optional[MurdockSettings] = MurdockSettings()
    ):
        self.uid : str = uuid.uuid4().hex
        self.config = config
        self.result : Optional[str] = None
        self.proc : Optional[Process] = None
        self.output : str = ""
        self.commit : CommitModel = commit
        self.ref : str = ref
        self.pr : PullRequestInfo = pr
        self.start_time : float = time.time()
        self.stop_time  : float = 0
        self.canceled : bool = False
        self.status : dict = { "status": "" }
        if self.pr is not None:
            self.fasttracked : bool = any(
                label in CI_CONFIG.fasttrack_labels for label in pr.labels
            )
        else:
            self.fasttracked : bool = False
        self.token : str = secrets.token_urlsafe(32)
        self.scripts_dir : str = GLOBAL_CONFIG.scripts_dir
        self.work_dir : str = os.path.join(GLOBAL_CONFIG.work_dir, self.uid)
        self.http_dir : str = os.path.join("results", self.uid)
        self.output_url : str = os.path.join(
            GLOBAL_CONFIG.base_url, self.http_dir, "output.html"
        )

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
            runtime_format = "%dd:%Hh:%Mm:%Ss"
        elif self.runtime > 3600:
            runtime_format = "%Hh:%Mm:%Ss"
        elif self.runtime > 60:
            runtime_format = "%Mm:%Ss"
        else:
            runtime_format = "%Ss"
        return time.strftime(runtime_format, time.gmtime(self.runtime))

    def queued_model(self):
        return JobModel(
            uid=self.uid,
            commit=self.commit,
            ref=self.ref,
            prinfo=self.pr,
            since=self.start_time,
            fasttracked=self.fasttracked
        )

    def running_model(self):
        return JobModel(
            uid=self.uid,
            commit=self.commit,
            ref=self.ref,
            prinfo=self.pr,
            since=self.start_time,
            status=self.status
        )

    @staticmethod
    def to_db_entry(job):
        return FinishedJobModel(
            uid=job.uid,
            commit=job.commit,
            since= job.start_time,
            runtime=job.runtime,
            result=job.result,
            output=job.output,
            output_url=job.output_url,
            status=job.status,
            prinfo=job.pr if job.pr is not None else None,
            ref=job.ref,
        ).dict(exclude_none=True)

    @staticmethod
    def from_db_entry(entry: dict) -> FinishedJobModel:
        return FinishedJobModel(**entry)

    @property
    def env(self):
        _env = { 
            "CI_SCRIPTS_DIR" : GLOBAL_CONFIG.scripts_dir,
            "CI_BUILD_HTTP_ROOT" : self.http_dir,
            "CI_BASE_URL": GLOBAL_CONFIG.base_url,
            "CI_JOB_UID": self.uid,
        }

        if self.config.env is not None:
            _env.update(self.config.env)

        if self.pr is not None:
            _env.update({
                "CI_PULL_COMMIT" : self.commit.sha,
                "CI_PULL_REPO" : GITHUB_CONFIG.repo,
                "CI_PULL_NR" : str(self.pr.number),
                "CI_PULL_URL" : self.pr.url,
                "CI_PULL_TITLE" : self.pr.title,
                "CI_PULL_USER" : self.pr.user,
                "CI_BASE_REPO" : self.pr.base_repo,
                "CI_BASE_BRANCH" : self.pr.base_branch,
                "CI_BASE_COMMIT" : self.pr.base_commit,
                "CI_PULL_LABELS" : ";".join(self.pr.labels),
            })
            if self.pr.mergeable:
                _env.update({"CI_MERGE_COMMIT": self.pr.merge_commit})
        if self.ref is not None:
            _env.update({
                "CI_BUILD_COMMIT" : self.commit.sha,
                "CI_BUILD_REF" : self.ref,
            })

        if GLOBAL_CONFIG.use_job_token:
            _env.update({
                "CI_API_TOKEN": self.token,
            })

        return _env

    def __repr__(self) -> str:
        ret = f"sha:{self.commit.sha[0:7]}"
        if self.pr is not None:
            ret += f" (PR #{self.pr.number})"
        if self.ref is not None:
            ret += f" ({self.ref})"
        return ret

    def __eq__(self, other) -> bool:
        return other is not None and self.uid == other.uid

    async def execute(self, notify=None):
        MurdockJob.create_dir(self.work_dir)
        LOGGER.debug(f"Launching build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(self.scripts_dir, "build.sh"), "build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        while True:
            data = await self.proc.stdout.readline()
            if not data:
                break
            self.output += data.decode()
            if notify is not None:
                await notify(json.dumps({
                    "cmd": "output",
                    "uid": self.uid,
                    "output": self.output
                }))
        await self.proc.wait()
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

        # If the job was stopped, just return now and skip the post_build action
        if self.result == "stopped":
            LOGGER.debug(f"Job {self} stopped before post_build action")
            self.proc = None
            return

        # Store build output in text file
        try:
            with open(os.path.join(self.work_dir, "output.txt"), "w") as out:
                out.write(self.output)
        except Exception as exc:
            LOGGER.warning(
                f"Job error for {self}: cannot write output.txt: {exc}"
            )

        # Remove build subdirectory
        MurdockJob.remove_dir(os.path.join(self.work_dir, "build"))

        LOGGER.debug(f"Launch post_build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(self.scripts_dir, "build.sh"), "post_build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await self.proc.communicate()
        if self.proc.returncode not in [
            0,
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            LOGGER.warning(
                f"Job error for {self}: Post build action failed:\n"
                f"out: {out.decode()}"
            )
            self.result = "errored"
        if self.proc.returncode in [
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            self.result = "stopped"
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
