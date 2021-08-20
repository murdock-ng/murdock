import os

from typing import List
from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    murdock_base_url: str = Field(
        env="MURDOCK_BASE_URL", default="https://ci.riot-os.org"
    )
    murdock_root_dir: str = Field(
        env="MURDOCK_ROOT_DIR", default="/var/lib/murdock-data"
    )
    murdock_scripts_dir: str = Field(
        env="MURDOCK_SCRIPTS_DIR", default="/var/lib/murdock-scripts"
    )
    murdock_use_job_token: bool = Field(
        env="MURDOCK_USE_JOB_TOKEN", default=False
    )
    murdock_num_workers: int = Field(
        env="MURDOCK_NUM_WORKERS", default=1
    )
    murdock_log_level: str = Field(
        env="MURDOCK_LOG_LEVEL", default="INFO"
    )
    murdock_db_host: str = Field(
        env="MURDOCK_DB_HOST", default="localhost"
    )
    murdock_db_port: int = Field(
        env="MURDOCK_DB_PORT", default=27017
    )
    murdock_db_name: str = Field(
        env="MURDOCK_DB_NAME", default="murdock"
    )
    murdock_max_finished_length_default: int = Field(
        env="MURDOCK_MAX_FINISHED_LENGTH_DEFAULT", default=25
    )
    murdock_github_app_client_id: str = Field(
        env="MURDOCK_GITHUB_APP_CLIENT_ID"
    )
    murdock_github_app_client_secret: str = Field(
        env="MURDOCK_GITHUB_APP_CLIENT_SECRET"
    )
    github_repo: str = Field(
        env="GITHUB_REPO"
    )
    github_webhook_secret: str = Field(
        env="GITHUB_WEBHOOK_SECRET"
    )
    github_api_token: str = Field(
        env="GITHUB_API_TOKEN"
    )
    ci_cancel_on_update: bool = Field(
        env="CI_CANCEL_ON_UPDATE", default=True
    )
    ci_ready_label: str = Field(
        env="CI_READY_LABEL", default="CI: ready for build"
    )
    ci_fasttrack_labels: List[str] = Field(
        env="CI_FASTTRACK_LABELS",
        default=["CI: skip compile test", "Process: release backport"]
    )
    ci_skip_keywords: List[str] = Field(
        env="CI_SKIP_KEYWORDS",
        default=["ci: skip", "ci: no", "ci: ignore"]
    )

    @validator("murdock_root_dir")
    def murdock_root_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(
                f"'MURDOCK_ROOT_DIR' doesn't exist ({path})"
            )
        return path

    @validator("murdock_scripts_dir")
    def murdock_scripts_dir_exists(cls, path):
        if not os.path.exists(path):
            raise ValueError(
                f"'MURDOCK_SCRIPTS_DIR' doesn't exist ({path})"
            )
        return path


CONFIG = Settings()
