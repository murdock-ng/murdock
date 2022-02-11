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
from murdock.models import PullRequestInfo, CommitModel, JobModel
from murdock.config import MurdockSettings


class MurdockJob:
    def __init__(
        self,
        commit: CommitModel,
        ref: Optional[str] = None,
        pr: Optional[PullRequestInfo] = None,
        config: Optional[MurdockSettings] = MurdockSettings(),
    ):
        self.uid: str = uuid.uuid4().hex
        self.config = config
        self.state = None
        self.proc: Optional[Process] = None
        self.output: str = ""
        self.commit: CommitModel = commit
        self.ref: str = ref
        self.pr: PullRequestInfo = pr
        self.start_time: float = time.time()
        self.stop_time: float = 0
        self.canceled: bool = False
        self.status: dict = {"status": ""}
        if self.pr is not None:
            self.fasttracked: bool = any(
                label in CI_CONFIG.fasttrack_labels for label in pr.labels
            )
        else:
            self.fasttracked: bool = False
        self.token: str = secrets.token_urlsafe(32)
        self.scripts_dir: str = GLOBAL_CONFIG.scripts_dir
        self.work_dir: str = os.path.join(GLOBAL_CONFIG.work_dir, self.uid)
        self.http_dir: str = os.path.join("results", self.uid)
        self.output_text_url: Optional[str] = None
        self.details_url = os.path.join(GLOBAL_CONFIG.base_url, "details", self.uid)
        self.output_url = os.path.join(
            GLOBAL_CONFIG.base_url, "results", self.uid, "output"
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
            LOGGER.debug(f"Directory '{work_dir}' doesn't exist, cannot remove")

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
            state=self.state,
            fasttracked=self.fasttracked,
        )

    def running_model(self):
        return JobModel(
            uid=self.uid,
            commit=self.commit,
            ref=self.ref,
            prinfo=self.pr,
            since=self.start_time,
            status=self.status,
            state=self.state,
            output=self.output,
        )

    @staticmethod
    def to_db_entry(job):
        return JobModel(
            uid=job.uid,
            commit=job.commit,
            since=job.start_time,
            runtime=job.runtime,
            state=job.state,
            output_text_url=job.output_text_url,
            status=job.status,
            prinfo=job.pr,
            ref=job.ref,
        ).dict(exclude_none=True)

    @staticmethod
    def finished_model(entry: dict) -> JobModel:
        return JobModel(**entry)

    @property
    def env(self):
        _env = {
            "CI_SCRIPTS_DIR": GLOBAL_CONFIG.scripts_dir,
            "CI_BASE_URL": GLOBAL_CONFIG.base_url,
            "CI_JOB_UID": self.uid,
            "CI_JOB_TOKEN": self.token,
        }

        if self.config.env is not None:
            _env.update(self.config.env)

        if self.pr is not None:
            _env.update(
                {
                    "CI_PULL_COMMIT": self.commit.sha,
                    "CI_PULL_REPO": GITHUB_CONFIG.repo,
                    "CI_PULL_NR": str(self.pr.number),
                    "CI_PULL_URL": self.pr.url,
                    "CI_PULL_TITLE": self.pr.title,
                    "CI_PULL_USER": self.pr.user,
                    "CI_BASE_REPO": self.pr.base_repo,
                    "CI_BASE_BRANCH": self.pr.base_branch,
                    "CI_BASE_COMMIT": self.pr.base_commit,
                    "CI_PULL_LABELS": ";".join(self.pr.labels),
                }
            )
            if self.pr.mergeable and self.pr.merge_commit is not None:
                _env.update({"CI_MERGE_COMMIT": self.pr.merge_commit})
        if self.ref is not None:
            _env.update(
                {
                    "CI_BUILD_COMMIT": self.commit.sha,
                    "CI_BUILD_REF": self.ref,
                }
            )
            if self.ref.startswith("refs/tags"):
                _env.update({"CI_BUILD_TAG": self.ref[10:]})
            if self.ref.startswith("refs/heads"):
                _env.update({"CI_BUILD_BRANCH": self.ref[11:]})

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

    def __hash__(self) -> int:
        return hash(self.uid)

    async def execute(self, notify=None):
        MurdockJob.create_dir(self.work_dir)
        script_path = os.path.join(self.scripts_dir, GLOBAL_CONFIG.script_name)
        LOGGER.debug(f"Launching run action for {self} (script: {script_path})")
        self.proc = await asyncio.create_subprocess_exec(
            script_path,
            "run",
            cwd=self.work_dir,
            env=self.env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            data = await self.proc.stdout.readline()
            if not data:
                break
            line = data.decode()
            self.output += line
            if notify is not None:
                await notify(
                    json.dumps({"cmd": "output", "uid": self.uid, "line": line})
                )
        await self.proc.wait()
        if self.proc.returncode == 0:
            self.state = "passed"
        elif (
            self.proc.returncode
            in [
                int(signal.SIGINT) * -1,
                int(signal.SIGKILL) * -1,
                int(signal.SIGTERM) * -1,
            ]
            and self.state == "running"
        ):
            self.state = "stopped"
        else:
            self.state = "errored"
        LOGGER.debug(f"Job {self} {self.state} (ret: {self.proc.returncode})")

        # Store job output in text file
        output_text_path = os.path.join(self.work_dir, "output.txt")
        try:
            with open(output_text_path, "w") as out:
                out.write(self.output)
        except Exception as exc:
            LOGGER.warning(f"Job error for {self}: cannot write output.txt: {exc}")

        output_text_url = os.path.join(
            GLOBAL_CONFIG.base_url, self.http_dir, "output.txt"
        )
        if os.path.exists(output_text_path):
            self.output_text_url = output_text_url

        # If the job was stopped, just return now and skip the finalize step
        if self.state == "stopped":
            LOGGER.debug(f"Job {self} stopped before finalizing")
            self.proc = None
            return

        # Remove build subdirectory
        MurdockJob.remove_dir(os.path.join(self.work_dir, "build"))

        LOGGER.debug(f"Finalizing {self}")
        self.proc = await asyncio.create_subprocess_exec(
            script_path,
            "finalize",
            cwd=self.work_dir,
            env=self.env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await self.proc.communicate()
        if self.proc.returncode not in [
            0,
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            LOGGER.warning(
                f"Job error: failed to finalize {self}:\n" f"out: {out.decode()}"
            )
            self.state = "errored"
        if self.proc.returncode in [
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            self.state = "stopped"

        self.proc = None

    async def stop(self):
        LOGGER.debug(f"Job {self} immediate stop requested")
        if self.proc is not None and self.proc.returncode is None:
            LOGGER.debug(f"Send signal {signal.SIGINT} to job {self}")
            os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                LOGGER.debug(f"Couldn't stop job {self} with {signal.SIGINT}")
        if not GLOBAL_CONFIG.store_stopped_jobs:
            LOGGER.debug(f"Removing job working directory '{self.work_dir}'")
            MurdockJob.remove_dir(self.work_dir)
