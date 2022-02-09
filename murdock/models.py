from datetime import datetime
from datetime import time as dtime
from typing import Optional, List

from pydantic import BaseModel, Field

from murdock.config import GLOBAL_CONFIG


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
    user: str = Field(
        None,
        title="Github user corresponding to the pull request author",
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


class CommitModel(BaseModel):
    sha: str = Field(
        None,
        title="SHA value of the commit to process",
    )
    message: str = Field(
        None,
        title="Commit message",
    )
    author: str = Field(
        None,
        title="Author of the commit",
    )


class JobModel(BaseModel):
    uid: str = Field(
        None,
        title="Unique identifier of the job (hex format)",
    )
    commit: CommitModel = Field(
        None,
        title="Information of the commit to process",
    )
    ref: Optional[str] = Field(
        None,
        title="Reference (if any), can be branch name or tag name",
    )
    prinfo: Optional[PullRequestInfo] = Field(
        None,
        title="Pull Request detailed information (if any)",
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
    state: str = Field(
        None,
        title="State of a job (queued, running, passed, errored or stopped)",
    )
    output: Optional[str] = Field(
        None,
        title="Output of the job",
    )
    output_text_url: Optional[str] = Field(
        None,
        title="URL where text output of the job is available",
    )
    runtime: Optional[float] = Field(
        None,
        title="Runtime of the job",
    )


class CategorizedJobsModel(BaseModel):
    queued: List[JobModel] = Field(
        None,
        title="List of all queued jobs",
    )
    running: List[JobModel] = Field(
        None,
        title="List of all running jobs",
    )
    finished: List[JobModel] = Field(
        None,
        title="List of all finished jobs",
    )


class ManualJobModel(BaseModel):
    ref: str
    is_tag: bool = (Field(False, title="Use tag commit if true, branch otherwise"),)
    sha: Optional[str] = Field(
        None, title="Specific commit SHA, if none, use HEAD. Ignore if is_tag is True"
    )


class JobQueryModel(BaseModel):
    limit: Optional[int] = Field(
        GLOBAL_CONFIG.max_finished_length_default,
        title="Limit length of items returned",
    )
    uid: Optional[str] = Field(None, title="uid of the job")
    is_pr: Optional[bool] = Field(
        None,
        title="Whether the job is about a PR",
    )
    is_branch: Optional[bool] = Field(
        None,
        title="Whether the job is about a branch",
    )
    is_tag: Optional[bool] = Field(
        None,
        title="Whether the job is about a tag",
    )
    states: Optional[str] = Field(
        None,
        title=(
            "space separated list of job states (queued, running, passed, "
            "errored, stopped)"
        ),
    )
    prnum: Optional[int] = Field(
        None,
        title="PR number",
    )
    branch: Optional[str] = Field(
        None,
        title="Name of the branch",
    )
    tag: Optional[str] = Field(
        None,
        title="Name of the tag",
    )
    ref: Optional[str] = Field(
        None,
        title="Full ref path",
    )
    sha: Optional[str] = Field(
        None,
        title="Commit SHA",
    )
    author: Optional[str] = Field(
        None,
        title="Author of the commit",
    )
    after: Optional[str] = Field(
        None,
        title="Date after which the job finished",
    )
    before: Optional[str] = Field(
        None,
        title="Date before which the job finished (included)",
    )

    def to_date_after_timestamp(self):
        return datetime.strptime(self.after, "%Y-%m-%d").timestamp()

    def to_date_before_timestamp(self):
        return datetime.combine(
            datetime.strptime(self.before, "%Y-%m-%d"),
            dtime(hour=23, minute=59, second=59, microsecond=999),
        ).timestamp()

    def to_mongodb_query(self):
        _query = {}
        if self.uid is not None:
            _query.update({"uid": self.uid})
        if self.is_pr is not None:
            _query.update({"prinfo": {"$exists": self.is_pr}})
        if self.is_branch is not None:
            if self.is_branch is True:
                _query.update({"ref": {"$regex": "^refs/heads/.*"}})
            else:
                _query.update({"ref": {"$not": {"$regex": "^refs/heads/.*"}}})
        if self.is_tag is not None:
            if self.is_tag is True:
                _query.update({"ref": {"$regex": "^refs/tags/.*"}})
            else:
                _query.update({"ref": {"$not": {"$regex": "^refs/tags/.*"}}})
        if self.states is not None:
            _query.update({"state": {"$in": self.states.split(" ")}})
        if self.prnum is not None:
            _query.update({"prinfo.number": self.prnum})
        if self.branch is not None:
            _query.update({"ref": f"refs/heads/{self.branch}"})
        if self.tag is not None:
            _query.update({"ref": f"refs/tags/{self.tag}"})
        if self.ref is not None:
            _query.update({"ref": self.ref})
        if self.sha is not None:
            _query.update({"commit.sha": self.sha})
        if self.author is not None:
            _query.update({"commit.author": self.author})
        if self.after is not None:
            _query.update({"since": {"$gte": self.to_date_after_timestamp()}})
        if self.before is not None:
            timestamp = self.to_date_before_timestamp()
            if "since" in _query:
                _query["since"].update({"$lte": timestamp})
            else:
                _query.update({"since": {"$lte": timestamp}})
        return _query
