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


UNSAFE_ENVS = ["CI_JOB_TOKEN", "CI_SCRIPTS_DIR"]


class MurdockJob:
    def __init__(
        self,
        commit: CommitModel,
        ref: Optional[str] = None,
        pr: Optional[PullRequestInfo] = None,
        config: Optional[MurdockSettings] = MurdockSettings(),
        trigger: Optional[str] = "api",
        triggered_by: Optional[str] = None,
        user_env: Optional[dict] = None,
    ):
        self.trigger: Optional[str] = trigger
        self.triggered_by: Optional[str] = triggered_by
        self.user_env: Optional[dict] = user_env
        self.uid: str = uuid.uuid4().hex
        self.config = config
        self.state: Optional[str] = None
        self.proc: Optional[Process] = None
        self.output: str = ""
        self.commit: CommitModel = commit
        self.ref: Optional[str] = ref
        self.pr: Optional[PullRequestInfo] = pr
        self.creation_time: float = time.time()
        self.start_time: float = 0
        self.stop_time: float = 0
        self.canceled: bool = False
        self.status: dict = {"status": ""}
        self.fasttracked: bool = (
            any(label in CI_CONFIG.fasttrack_labels for label in self.pr.labels)
            if self.pr is not None
            else False
        )  # type: ignore[union-attr]
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
    def create_dir(work_dir: str) -> None:
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

    def model(self) -> JobModel:
        return JobModel(
            uid=self.uid,
            commit=self.commit,
            creation_time=self.creation_time,
            start_time=self.start_time,
            runtime=self.runtime,
            state=self.state,
            output=self.output,
            output_text_url=self.output_text_url,
            status=self.status,
            prinfo=self.pr,
            ref=self.ref,
            fasttracked=self.fasttracked,
            trigger=self.trigger,
            triggered_by=self.triggered_by,
            env=self.safe_env,
            user_env=self.user_env,
        )

    @staticmethod
    def to_db_entry(job):
        return job.model().dict(exclude_none=True)

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
            "CI_BUILD_TREE": self.commit.tree,
        }

        if self.config.env is not None:
            _env.update(self.config.env)

        if self.user_env is not None:
            _env.update(self.user_env)

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
                    "CI_BUILD_REPO": GITHUB_CONFIG.repo,
                }
            )
            if self.ref.startswith("refs/tags"):
                _env.update({"CI_BUILD_TAG": self.ref[10:]})
            if self.ref.startswith("refs/heads"):
                _env.update({"CI_BUILD_BRANCH": self.ref[11:]})

        return _env

    @property
    def safe_env(self):
        _env = self.env.copy()
        for var in UNSAFE_ENVS:
            _env.pop(var)
        return _env

    @property
    def title(self):
        commit = self.commit.sha[0:7]
        if self.pr is not None:
            return f"PR #{self.pr.number} ({commit})"
        elif self.ref is not None and self.ref.startswith("refs/tags"):
            return f"tag {self.ref[10:]} ({commit})"
        elif self.ref is not None and self.ref.startswith("refs/heads"):
            return f"branch {self.ref[11:]} ({commit})"
        else:
            return f"commit {commit}"

    def __repr__(self) -> str:
        return f"job {self.uid[0:7]} - {self.title}"

    def __eq__(self, other) -> bool:
        return other is not None and self.uid == other.uid

    def __hash__(self) -> int:
        return hash(self.uid)

    async def execute(self, notify=None) -> None:
        MurdockJob.create_dir(self.work_dir)
        script_path = os.path.join(self.scripts_dir, GLOBAL_CONFIG.script_name)
        LOGGER.debug(f"Launching run action for {self} (script: {script_path})")
        self.proc = await asyncio.create_subprocess_exec(
            script_path,
            cwd=self.work_dir,
            env=self.env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            data = await self.proc.stdout.readline()  # type: ignore[union-attr]
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
        LOGGER.debug(f"{self} {self.state} (ret: {self.proc.returncode})")

        # Store job output in text file
        output_text_path = os.path.join(self.work_dir, "output.txt")
        try:
            with open(output_text_path, "w") as out:
                out.write(self.output)
        except Exception as exc:
            LOGGER.warning(f"Error for {self}: cannot write output.txt: {exc}")

        output_text_url = os.path.join(
            GLOBAL_CONFIG.base_url, self.http_dir, "output.txt"
        )
        if os.path.exists(output_text_path):
            self.output_text_url = output_text_url

        self.proc = None

    async def stop(self) -> None:
        LOGGER.debug(f"{self} immediate stop requested")
        if self.proc is not None and self.proc.returncode is None:
            LOGGER.debug(f"Send signal {signal.SIGINT} to {self}")
            os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                LOGGER.debug(f"Couldn't stop {self} with {signal.SIGINT}")
        if not GLOBAL_CONFIG.store_stopped_jobs:
            LOGGER.debug(f"Removing job working directory '{self.work_dir}'")
            MurdockJob.remove_dir(self.work_dir)
