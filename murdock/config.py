import os

from typing import List
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
    skip_keywords: List[str] = Field(
        env="CI_SKIP_KEYWORDS",
        default=["ci: skip", "ci: no", "ci: ignore"]
    )


class MurdockSettings(BaseSettings):
    base_url: str = Field(
        env="MURDOCK_BASE_URL", default="https://ci.riot-os.org"
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
    accepted_branches: List[str] = Field(
        env="MURDOCK_ACCEPTED_BRANCHES", default=["*"]
    )
    accepted_tags: List[str] = Field(
        env="MURDOCK_ACCEPTED_TAGS", default=["*"]
    )
    enable_comments: bool = Field(
        env="MURDOCK_ENABLE_COMMENTS", default=True
    )
    use_sticky_comment: bool = Field(
        env="MURDOCK_USE_STICKY_COMMENT", default=False
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
        env="MURCOCK_CANCEL_ON_UPDATE", default=True
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


_ENV_FILE = os.getenv(
    "ENV_FILE", os.path.join(os.path.dirname(__file__), "..", ".env")
)

DB_CONFIG = DatabaseSettings(_env_file=_ENV_FILE)
GITHUB_CONFIG = GithubSettings(_env_file=_ENV_FILE)
CI_CONFIG = CISettings(_env_file=_ENV_FILE)
MURDOCK_CONFIG = MurdockSettings(_env_file=_ENV_FILE)
