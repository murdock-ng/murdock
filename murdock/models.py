from typing import Optional, List

from pydantic import BaseModel


class PullRequestInfo(BaseModel):
    title: str
    number: int
    merge_commit: str
    branch: str
    commit: str
    user: str
    url: str
    base_repo: str
    base_branch: str
    base_commit: str
    base_full_name: str
    mergeable: bool
    labels: list[str]


class JobModel(BaseModel):
    uid: str
    prinfo: PullRequestInfo
    since: float
    fasttracked: Optional[bool]
    status: Optional[dict]


class FinishedJobModel(JobModel):
    result: str
    output_url: str
    runtime: float
    work_dir: str


class CategorizedJobsModel(BaseModel):
    queued: List[JobModel]
    building: List[JobModel]
    finished: List[FinishedJobModel]
