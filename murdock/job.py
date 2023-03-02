import os
import secrets
import shutil
import time
import uuid

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from murdock.config import GLOBAL_CONFIG, CI_CONFIG, GITHUB_CONFIG
from murdock.log import LOGGER
from murdock.models import PullRequestInfo, CommitModel, JobModel
from murdock.config import MurdockSettings
from murdock.task import Task


UNSAFE_ENVS = ["CI_JOB_TOKEN", "CI_SCRIPTS_DIR"]


class MurdockJob:
    def __init__(
        self,
        commit: CommitModel,
        ref: Optional[str] = None,
        pr: Optional[PullRequestInfo] = None,
        config: MurdockSettings = MurdockSettings(),
        trigger: Optional[str] = "api",
        triggered_by: Optional[str] = None,
        user_env: Optional[dict] = None,
    ):
        self.trigger: Optional[str] = trigger
        self.triggered_by: Optional[str] = triggered_by
        self.user_env: Optional[dict] = user_env
        self._uuid: uuid.UUID = uuid.uuid4()
        self.config = config
        self.state: Optional[str] = None
        self.current_task: Optional[Task] = None
        self.output: str = ""
        self.notify = lambda _, __: None  # Notify do nothing by default
        self.commit: CommitModel = commit
        self.ref: Optional[str] = ref
        self.pr: Optional[PullRequestInfo] = pr
        self.creation_time: datetime = datetime.now(timezone.utc)
        self._start_time: datetime = datetime.fromtimestamp(0, tz=timezone.utc)
        self._stop_time: datetime = datetime.fromtimestamp(0, tz=timezone.utc)
        self.canceled: bool = False
        self.status: dict = {"status": ""}
        self.fasttracked: bool = (
            any(label in CI_CONFIG.fasttrack_labels for label in self.pr.labels)
            if self.pr is not None
            else False
        )  # type: ignore[union-attr]
        self.artifacts: Optional[List[str]] = None
        self.token: str = secrets.token_urlsafe(32)
        self.output_text_url: Optional[str] = None
        self.output_url = os.path.join(
            GLOBAL_CONFIG.base_url, "results", self.uid, "output"
        )
        self._logger_context = {
            "job": str(self.uuid),
            "job_description": str(self),
            "commit": self.commit.sha,
        }
        if self.pr is not None:
            self._logger_context["pr"] = str(self.pr.number)
        self._logger = LOGGER.bind(**self._logger_context)

    def create_dir(self) -> None:
        logger = self._logger.bind(dir=str(self.work_dir))
        try:
            logger.info("Creating work directory")
            os.makedirs(self.work_dir)
        except FileExistsError:
            logger.info("Directory already exists, recreating")
            shutil.rmtree(self.work_dir)
            os.makedirs(self.work_dir)

    @staticmethod
    def remove_dir(work_dir):
        logger = LOGGER.bind(dir=str(work_dir))
        logger.info("Removing work directory")
        try:
            shutil.rmtree(work_dir)
        except FileNotFoundError:
            logger.warning("Work directory doesn't exist, cannot remove")

    @property
    def start_time(self) -> datetime:
        return self._start_time

    @property
    def stop_time(self) -> datetime:
        return self._stop_time

    @property
    def runtime(self) -> timedelta:
        return self.stop_time - self.start_time

    @property
    def runtime_human(self) -> str:
        runtime = self.runtime.total_seconds()
        if runtime > 86400:
            runtime_format = "%dd:%Hh:%Mm:%Ss"
        elif runtime > 3600:
            runtime_format = "%Hh:%Mm:%Ss"
        elif runtime > 60:
            runtime_format = "%Mm:%Ss"
        else:
            runtime_format = "%Ss"
        return time.strftime(runtime_format, time.gmtime(runtime))

    @property
    def uid(self) -> str:
        return self.uuid.hex

    @property
    def uuid(self) -> uuid.UUID:
        return self._uuid

    @property
    def scripts_dir(self) -> str:
        return GLOBAL_CONFIG.scripts_dir

    @property
    def work_dir(self) -> str:
        return os.path.join(GLOBAL_CONFIG.work_dir, self.uid)

    @property
    def http_dir(self) -> str:
        return os.path.join("results", self.uid)

    @property
    def details_url(self) -> str:
        return os.path.join(GLOBAL_CONFIG.base_url, "details", self.uid)

    def model(self) -> JobModel:
        return JobModel(
            uid=self.uid,
            commit=self.commit,
            creation_time=self.creation_time.timestamp(),
            start_time=self.start_time.timestamp(),
            runtime=self.runtime.total_seconds(),
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
            artifacts=self.artifacts,
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
            "CI_MURDOCK_PROJECT": GLOBAL_CONFIG.project,
            "CI_BASE_URL": GLOBAL_CONFIG.base_url,
            "CI_JOB_UID": self.uid,
            "CI_JOB_TOKEN": self.token,
            "CI_BUILD_TREE": self.commit.tree,
        }

        _env.update(GLOBAL_CONFIG.custom_env)
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

    def set_start_time(self, start_time: datetime):
        if start_time.tzinfo is None:
            raise ValueError("Incomplete time object, no time zone defined")
        self._start_time = start_time

    def set_stop_time(self, stop_time: datetime):
        if stop_time.tzinfo is None:
            raise ValueError("Incomplete time object, no time zone defined")
        self._stop_time = stop_time

    async def extend_job_output(self, line):
        self.output += line
        if self.notify is not None:
            await self.notify(self, line)

    @property
    def logging_context(self):
        # Copy over the dict
        return dict(self._logger_context)

    async def exec(self, notify: Callable) -> None:
        self.create_dir()
        self._logger.debug("Starting execution")

        self.notify = notify
        for index, task_setting in enumerate(self.config.tasks):
            self.current_task = Task(
                index + 1,
                task_setting,
                self.uid,
                self.env,
                self.extend_job_output,
                self.scripts_dir,
                self.work_dir,
                logger=self._logger,
            )
            if len(self.config.tasks) > 1:
                self.output += f"-- Running {self.current_task} --\n"
            state = await self.current_task.exec()
            if len(self.config.tasks) > 1:
                self.output += f"-- {self.current_task} completed ({state}) --\n"
            self.state = state
            if state in ["stopped", "errored"]:
                self._logger.info(
                    "Stopping execution",
                    task=str(self.current_task),
                    state=state,
                )
                break

        # Store job output in text file
        output_text_path = os.path.join(self.work_dir, "output.txt")
        try:
            with open(output_text_path, "w") as out:
                out.write(self.output)
        except Exception as exc:
            self._logger.warning(
                "Cannot write output",
                file="output.txt",
                exception=str(exc),
            )

        output_text_url = os.path.join(
            GLOBAL_CONFIG.base_url, self.http_dir, "output.txt"
        )
        if os.path.exists(output_text_path):
            self.output_text_url = output_text_url

        self.current_task = None

        # Processing artifacts if any
        artifacts: List[str] = []
        for artifact in self.config.artifacts:
            artifact_path = os.path.join(self.work_dir, artifact)
            if os.path.exists(artifact_path):
                artifacts.append(artifact)
        if artifacts:
            self.artifacts = artifacts

    async def stop(self) -> None:
        self._logger.info("immediate stop requested")
        if self.current_task is not None:
            await self.current_task.stop()
        if not GLOBAL_CONFIG.store_stopped_jobs:
            MurdockJob.remove_dir(self.work_dir)
