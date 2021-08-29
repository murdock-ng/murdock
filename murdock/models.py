from typing import Optional, List

from pydantic import BaseModel, Field


class PullRequestInfo(BaseModel):
    title: str = Field(
        None,
        title="Pull Request title",
    )
    number: int = Field(
        None,
        title="Pull Request number",
    )
    merge_commit: str = Field(
        None,
        title="SHA value of the merged commit",
    )
    branch: str = Field(
        None,
        title="Branch name of the pull request",
    )
    commit: str = Field(
        None,
        title="SHA value of the pull request commit",
    )
    commit_message: str = Field(
        None,
        title="Commit message of the pull request commit",
    )
    user: str = Field(
        None,
        title="Github user corresponding to the commit author",
    )
    url: str = Field(
        None,
        title="Github URL of the pull request",
    )
    base_repo: str = Field(
        None,
        title="URL of the base repository",
    )
    base_branch: str = Field(
        None,
        title="Name of the target branch",
    )
    base_commit: str = Field(
        None,
        title="Last commit of the target branch",
    )
    base_full_name: str = Field(
        None,
        title="Target repository name",
    )
    mergeable: bool = Field(
        None,
        title="True if the pull request is mergeable, False otherwise",
    )
    labels: list[str] = Field(
        None,
        title="List of Github labels assigned to the pull request",
    )


class JobModel(BaseModel):
    uid: str = Field(
        None,
        title="Unique identifier of the job (hex format)",
    )
    prinfo: PullRequestInfo = Field(
        None,
        title="Pull Request detailed information",
    )
    since: float = Field(
        None,
        title="Time of last update of the job",
    )
    fasttracked: Optional[bool] = Field(
        None,
        title="Whether the job can be fasttracked",
    )
    status: Optional[dict] = Field(
        None,
        title="Status of the job",
    )


class FinishedJobModel(JobModel):
    result: str = Field(
        None,
        title="Final result of a job (passed or errored)",
    )
    output_url: str = Field(
        None,
        title="URL where html output of the job is available",
    )
    runtime: float = Field(
        None,
        title="Runtime of the job",
    )


class CategorizedJobsModel(BaseModel):
    queued: List[JobModel] = Field(
        None,
        title="List of all queued jobs",
    )
    building: List[JobModel] = Field(
        None,
        title="List of all building jobs",
    )
    finished: List[FinishedJobModel] = Field(
        None,
        title="List of all finished jobs",
    )
