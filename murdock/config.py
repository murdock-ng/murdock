import os
import tempfile

from typing import List, Optional, Dict
from pydantic import Field, validator
from pydantic_settings import BaseSettings


class DatabaseSettings(BaseSettings):
    type: str = Field(alias="MURDOCK_DB_TYPE", default="mongodb")
    host: str = Field(alias="MURDOCK_DB_HOST", default="localhost")
    port: int = Field(alias="MURDOCK_DB_PORT", default=0)
    name: str = Field(alias="MURDOCK_DB_NAME", default="murdock")
    user: str = Field(alias="MURDOCK_DB_AUTH_USER", default="murdock")
    password: str = Field(alias="MURDOCK_DB_AUTH_PASSWORD", default="hunter2")


class GithubSettings(BaseSettings):
    app_client_id: str = Field(alias="MURDOCK_GITHUB_APP_CLIENT_ID")
    app_client_secret: str = Field(alias="MURDOCK_GITHUB_APP_CLIENT_SECRET")
    repo: str = Field(alias="GITHUB_REPO")
    webhook_secret: str = Field(alias="GITHUB_WEBHOOK_SECRET")
    api_token: str = Field(alias="GITHUB_API_TOKEN")


class CISettings(BaseSettings):
    ready_label: str = Field(alias="CI_READY_LABEL", default="CI: ready for build")
    fasttrack_labels: List[str] = Field(
        alias="CI_FASTTRACK_LABELS",
        default=["CI: skip compile test", "Process: release backport"],
    )


class MailNotifierSettings(BaseSettings):
    recipients: List[str] = Field(alias="MURDOCK_NOTIFIER_MAIL_RECIPIENTS", default=[])
    server: str = Field(alias="MURDOCK_NOTIFIER_MAIL_SERVER", default="localhost")
    port: int = Field(alias="MURDOCK_NOTIFIER_MAIL_PORT", default=25)
    use_tls: bool = Field(alias="MURDOCK_NOTIFIER_MAIL_USE_TLS", default=True)
    username: str = Field(alias="MURDOCK_NOTIFIER_MAIL_USERNAME", default="")
    password: str = Field(alias="MURDOCK_NOTIFIER_MAIL_PASSWORD", default="")


class MatrixNotifierSettings(BaseSettings):
    room: str = Field(alias="MURDOCK_NOTIFIER_MATRIX_ROOM", default="")
    token: str = Field(alias="MURDOCK_NOTIFIER_MATRIX_TOKEN", default="")


class NotifierSettings(BaseSettings):
    pr: List[str] = Field(alias="MURDOCK_NOTIFIER_PR_NOTIFIERS", default=["matrix"])
    branch: List[str] = Field(
        alias="MURDOCK_NOTIFIER_BRANCH_NOTIFIERS", default=["mail", "matrix"]
    )
    tag: List[str] = Field(
        alias="MURDOCK_NOTIFIER_TAG_NOTIFIERS", default=["mail", "matrix"]
    )
    commit: List[str] = Field(
        alias="MURDOCK_NOTIFIER_COMMIT_NOTIFIERS", default=["matrix"]
    )


class GlobalSettings(BaseSettings):
    project: str = Field(alias="MURDOCK_PROJECT", default="default")
    base_url: str = Field(alias="MURDOCK_BASE_URL", default="http://localhost:8000")
    work_dir: str = Field(alias="MURDOCK_WORK_DIR", default="/var/lib/murdock-data")
    host_work_dir: str = Field(
        alias="MURDOCK_HOST_WORK_DIR",
        default=os.path.join(tempfile.gettempdir(), "murdock-data"),
    )
    scripts_dir: str = Field(
        alias="MURDOCK_SCRIPTS_DIR", default="/var/lib/murdock-scripts"
    )
    script_name: str = Field(alias="MURDOCK_SCRIPT_NAME", default="run.sh")
    run_in_docker: bool = Field(alias="MURDOCK_RUN_IN_DOCKER", default=False)
    docker_user_uid: int = Field(alias="MURDOCK_USER_UID", default=1000)
    docker_user_gid: int = Field(alias="MURDOCK_USER_GID", default=1000)
    docker_default_image: str = Field(
        alias="MURDOCK_DOCKER_DEFAULT_TASK_IMAGE", default="ubuntu:latest"
    )
    docker_volumes: dict = Field(alias="MURDOCK_DOCKER_VOLUMES", default=dict())
    docker_cpu_limit: float = Field(alias="MURDOCK_DOCKER_CPU_LIMIT", default=1.0)
    docker_mem_limit: str = Field(alias="MURDOCK_DOCKER_MEM_LIMIT", default="1g")
    accepted_events: List[str] = Field(
        alias="MURDOCK_ACCEPTED_EVENTS", default=["push", "pull_request"]
    )
    num_workers: int = Field(alias="MURDOCK_NUM_WORKERS", default=1)
    custom_env: Dict[str, str] = Field(alias="MURDOCK_CUSTOM_ENV", default=dict())
    log_level: str = Field(alias="MURDOCK_LOG_LEVEL", default="DEBUG")
    uvicorn_access_log_output: str = Field(alias="UVICORN_LOG_LEVEL", default="INFO")
    log_output: str = Field(alias="MURDOCK_LOG_OUTPUT", default="console")
    max_finished_length_default: int = Field(
        alias="MURDOCK_MAX_FINISHED_LENGTH_DEFAULT", default=25
    )
    cancel_on_update: bool = Field(alias="MURDOCK_CANCEL_ON_UPDATE", default=True)
    enable_commit_status: bool = Field(alias="MURDOCK_ENABLE_COMMIT_STATUS", default=True)
    commit_status_context: str = Field(
        alias="MURDOCK_COMMIT_STATUS_CONTEXT", default="Murdock"
    )
    enable_pr_comment: bool = Field(alias="MURDOCK_ENABLE_PR_COMMENT", default=True)
    store_stopped_jobs: bool = Field(alias="MURDOCK_STORE_STOPPED_JOBS", default=True)
    enable_notifications: bool = Field(alias="MURDOCK_NOTIFIER_ENABLE", default=False)

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


class ArtifactCommentSettings(BaseSettings):
    name: str = Field()
    readable_name: Optional[str] = Field(default=None)


class PRSettings(BaseSettings):
    enable_comments: bool = Field(default=False)
    sticky_comment: bool = Field(default=False)
    comment_artifacts: List[ArtifactCommentSettings] = Field(default=[])
    comment_footer: Optional[str] = Field(default=None)


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
    env: Dict[str, str] = Field(default=dict())
    failfast: bool = Field(default=False)
    artifacts: List[str] = Field(default=[])
    tasks: List[TaskSettings] = Field(default=[TaskSettings()])


_ENV_FILE = os.getenv("ENV_FILE", os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_CONFIG = DatabaseSettings(_env_file=_ENV_FILE)
GITHUB_CONFIG = GithubSettings(_env_file=_ENV_FILE)
CI_CONFIG = CISettings(_env_file=_ENV_FILE)
MAIL_NOTIFIER_CONFIG = MailNotifierSettings(_env_file=_ENV_FILE)
MATRIX_NOTIFIER_CONFIG = MatrixNotifierSettings(_env_file=_ENV_FILE)
NOTIFIER_CONFIG = NotifierSettings(_env_file=_ENV_FILE)
GLOBAL_CONFIG = GlobalSettings(_env_file=_ENV_FILE)
