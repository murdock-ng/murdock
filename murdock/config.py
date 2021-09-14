import os

from typing import List, Optional
from pydantic import BaseSettings, Field, validator


class DatabaseSettings(BaseSettings):
    host: str = Field(
        env="MURDOCK_DB_HOST", default="localhost"
    )
    port: int = Field(
        env="MURDOCK_DB_PORT", default=27017
    )
    name: str = Field(
        env="MURDOCK_DB_NAME", default="murdock"
    )


class GithubSettings(BaseSettings):
    app_client_id: str = Field(
        env="MURDOCK_GITHUB_APP_CLIENT_ID"
    )
    app_client_secret: str = Field(
        env="MURDOCK_GITHUB_APP_CLIENT_SECRET"
    )
    repo: str = Field(
        env="GITHUB_REPO"
    )
    webhook_secret: str = Field(
        env="GITHUB_WEBHOOK_SECRET"
    )
    api_token: str = Field(
        env="GITHUB_API_TOKEN"
    )


class CISettings(BaseSettings):
    ready_label: str = Field(
        env="CI_READY_LABEL", default="CI: ready for build"
    )
    fasttrack_labels: List[str] = Field(
        env="CI_FASTTRACK_LABELS",
        default=["CI: skip compile test", "Process: release backport"]
    )


class GlobalSettings(BaseSettings):
    base_url: str = Field(
        env="MURDOCK_BASE_URL", default="http://localhost:8000"
    )
    work_dir: str = Field(
        env="MURDOCK_WORK_DIR", default="/var/lib/murdock-data"
    )
    scripts_dir: str = Field(
        env="MURDOCK_SCRIPTS_DIR", default="/var/lib/murdock-scripts"
    )
    use_job_token: bool = Field(
        env="MURDOCK_USE_JOB_TOKEN", default=False
    )
    accepted_events: List[str] = Field(
        env="MURDOCK_ACCEPTED_EVENTS", default=["push", "pull_request"]
    )
    num_workers: int = Field(
        env="MURDOCK_NUM_WORKERS", default=1
    )
    log_level: str = Field(
        env="MURDOCK_LOG_LEVEL", default="INFO"
    )
    max_finished_length_default: int = Field(
        env="MURDOCK_MAX_FINISHED_LENGTH_DEFAULT", default=25
    )
    cancel_on_update: bool = Field(
        env="MURDOCK_CANCEL_ON_UPDATE", default=True
    )

    @validator("work_dir")
    def work_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(
                f"'MURDOCK_WORK_DIR' doesn't exist ({path})"
            )
        return path

    @validator("scripts_dir")
    def scripts_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(
                f"'MURDOCK_SCRIPTS_DIR' doesn't exist ({path})"
            )
        return path


class PushSettings(BaseSettings):
    branches: List[str] = Field(default=[])
    tags: List[str] = Field(default=[])


class PRSettings(BaseSettings):
    enable_comments: bool = Field(default=False)
    sticky_comment: bool = Field(default=False)


class CommitSettings(BaseSettings):
    skip_keywords: List[str] = Field(default=[])


class MurdockSettings(BaseSettings):
    push: PushSettings = Field(default=PushSettings())
    pr: PRSettings = Field(default=PRSettings())
    commit: CommitSettings = Field(default=CommitSettings())
    env: Optional[dict] = Field(default=None)


_ENV_FILE = os.getenv(
    "ENV_FILE", os.path.join(os.path.dirname(__file__), "..", ".env")
)

DB_CONFIG = DatabaseSettings(_env_file=_ENV_FILE)
GITHUB_CONFIG = GithubSettings(_env_file=_ENV_FILE)
CI_CONFIG = CISettings(_env_file=_ENV_FILE)
GLOBAL_CONFIG = GlobalSettings(_env_file=_ENV_FILE)
