import os

from typing import List, Optional
from pydantic import BaseSettings, Field, validator


class DatabaseSettings(BaseSettings):
    host: str = Field(env="MURDOCK_DB_HOST", default="localhost")
    port: int = Field(env="MURDOCK_DB_PORT", default=27017)
    name: str = Field(env="MURDOCK_DB_NAME", default="murdock")


class GithubSettings(BaseSettings):
    app_client_id: str = Field(env="MURDOCK_GITHUB_APP_CLIENT_ID")
    app_client_secret: str = Field(env="MURDOCK_GITHUB_APP_CLIENT_SECRET")
    repo: str = Field(env="GITHUB_REPO")
    webhook_secret: str = Field(env="GITHUB_WEBHOOK_SECRET")
    api_token: str = Field(env="GITHUB_API_TOKEN")


class CISettings(BaseSettings):
    ready_label: str = Field(env="CI_READY_LABEL", default="CI: ready for build")
    fasttrack_labels: List[str] = Field(
        env="CI_FASTTRACK_LABELS",
        default=["CI: skip compile test", "Process: release backport"],
    )


class MailNotifierSettings(BaseSettings):
    recipients: List[str] = Field(env="MURDOCK_NOTIFIER_MAIL_RECIPIENTS", default=[])
    server: str = Field(env="MURDOCK_NOTIFIER_MAIL_SERVER", default="localhost")
    port: int = Field(env="MURDOCK_NOTIFIER_MAIL_PORT", default=25)
    use_tls: bool = Field(env="MURDOCK_NOTIFIER_MAIL_USE_TLS", default=True)
    username: str = Field(env="MURDOCK_NOTIFIER_MAIL_USERNAME", default="")
    password: str = Field(env="MURDOCK_NOTIFIER_MAIL_PASSWORD", default="")


class MatrixNotifierSettings(BaseSettings):
    room: str = Field(env="MURDOCK_NOTIFIER_MATRIX_ROOM", default="")
    token: str = Field(env="MURDOCK_NOTIFIER_MATRIX_TOKEN", default="")


class NotifierSettings(BaseSettings):
    pr: List[str] = Field(env="MURDOCK_NOTIFIER_PR_NOTIFIERS", default=["matrix"])
    branch: List[str] = Field(
        env="MURDOCK_NOTIFIER_BRANCH_NOTIFIERS", default=["mail", "matrix"]
    )
    tag: List[str] = Field(
        env="MURDOCK_NOTIFIER_TAG_NOTIFIERS", default=["mail", "matrix"]
    )
    commit: List[str] = Field(
        env="MURDOCK_NOTIFIER_COMMIT_NOTIFIERS", default=["matrix"]
    )


class GlobalSettings(BaseSettings):
    project: str = Field(env="MURDOCK_PROJECT", default="default")
    base_url: str = Field(env="MURDOCK_BASE_URL", default="http://localhost:8000")
    work_dir: str = Field(env="MURDOCK_WORK_DIR", default="/var/lib/murdock-data")
    host_work_dir: str = Field(env="MURDOCK_HOST_WORK_DIR", default="/tmp/murdock-data")
    scripts_dir: str = Field(
        env="MURDOCK_SCRIPTS_DIR", default="/var/lib/murdock-scripts"
    )
    script_name: str = Field(env="MURDOCK_SCRIPT_NAME", default="run.sh")
    run_in_docker: bool = Field(env="MURDOCK_RUN_IN_DOCKER", default=False)
    docker_user_uid: int = Field(env="MURDOCK_USER_UID", default=1000)
    docker_user_gid: int = Field(env="MURDOCK_USER_GID", default=1000)
    docker_default_image: str = Field(
        env="MURDOCK_DOCKER_DEFAULT_TASK_IMAGE", default="ubuntu:latest"
    )
    docker_volumes: dict = Field(env="MURDOCK_DOCKER_VOLUMES", default=dict())
    accepted_events: List[str] = Field(
        env="MURDOCK_ACCEPTED_EVENTS", default=["push", "pull_request"]
    )
    num_workers: int = Field(env="MURDOCK_NUM_WORKERS", default=1)
    log_level: str = Field(env="MURDOCK_LOG_LEVEL", default="INFO")
    max_finished_length_default: int = Field(
        env="MURDOCK_MAX_FINISHED_LENGTH_DEFAULT", default=25
    )
    cancel_on_update: bool = Field(env="MURDOCK_CANCEL_ON_UPDATE", default=True)
    enable_commit_status: bool = Field(env="MURDOCK_ENABLE_COMMIT_STATUS", default=True)
    commit_status_context: str = Field(
        env="MURDOCK_COMMIT_STATUS_CONTEXT", default="Murdock"
    )
    store_stopped_jobs: bool = Field(env="MURDOCK_STORE_STOPPED_JOBS", default=True)
    enable_notifications: bool = Field(env="MURDOCK_NOTIFIER_ENABLE", default=False)

    @validator("work_dir")
    def work_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(f"Work dir doesn't exist ({path})")
        return path

    @validator("scripts_dir")
    def scripts_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(f"Scripts dir doesn't exist ({path})")
        return path


class PushSettings(BaseSettings):
    branches: List[str] = Field(default=[])
    tags: List[str] = Field(default=[])


class PRSettings(BaseSettings):
    enable_comments: bool = Field(default=False)
    sticky_comment: bool = Field(default=False)


class CommitSettings(BaseSettings):
    skip_keywords: List[str] = Field(default=[])


class TaskSettings(BaseSettings):
    name: Optional[str] = Field(default=None)
    image: Optional[str] = Field(default=None)
    command: Optional[str] = Field(default=None)
    env: dict = Field(default=dict())


class MurdockSettings(BaseSettings):
    push: PushSettings = Field(default=PushSettings())
    pr: PRSettings = Field(default=PRSettings())
    commit: CommitSettings = Field(default=CommitSettings())
    env: Optional[dict] = Field(default=None)
    failfast: Optional[bool] = Field(default=False)
    tasks: List[TaskSettings] = Field(default=[TaskSettings()])


_ENV_FILE = os.getenv("ENV_FILE", os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_CONFIG = DatabaseSettings(_env_file=_ENV_FILE)
GITHUB_CONFIG = GithubSettings(_env_file=_ENV_FILE)
CI_CONFIG = CISettings(_env_file=_ENV_FILE)
MAIL_NOTIFIER_CONFIG = MailNotifierSettings(_env_file=_ENV_FILE)
MATRIX_NOTIFIER_CONFIG = MatrixNotifierSettings(_env_file=_ENV_FILE)
NOTIFIER_CONFIG = NotifierSettings(_env_file=_ENV_FILE)
GLOBAL_CONFIG = GlobalSettings(_env_file=_ENV_FILE)
