import pytest

from ..database import Database
from ..job import MurdockJob
from ..models import CommitModel, PullRequestInfo, JobQueryModel


commit = CommitModel(
    sha="test_commit", tree="test_tree", message="test message", author="test_user"
)
prinfo = PullRequestInfo(
    title="test",
    number=123,
    merge_commit="test_merge_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=True,
    labels=["test"],
)


@pytest.mark.asyncio
@pytest.mark.usefixtures("mongo")
async def test_database(caplog):
    db = Database()
    await db.init()
    job_pr = MurdockJob(commit, pr=prinfo)
    await db.insert_job(job_pr)
    search_job = await db.find_job("123")
    assert search_job is None
    search_job = await db.find_job(job_pr.uid)
    assert search_job is not None
    assert search_job.commit == job_pr.commit
    assert search_job.pr == job_pr.pr
    assert search_job.user_env is None

    search_jobs = await db.find_jobs(JobQueryModel(prnum=456))
    assert len(search_jobs) == 0
    assert await db.count_jobs(JobQueryModel(prnum=456)) == 0

    assert await db.count_jobs(JobQueryModel(prnum=123)) == 1
    search_jobs = await db.find_jobs(JobQueryModel(prnum=123))
    assert len(search_jobs) == 1
    assert search_jobs[0].commit == job_pr.commit
    assert search_jobs[0].prinfo == job_pr.pr

    assert await db.update_jobs(JobQueryModel(prnum=123), "commit.sha", "456") == 1
    search_jobs = await db.find_jobs(JobQueryModel(prnum=123))
    assert search_jobs[0].commit.sha == "456"

    await db.delete_jobs(JobQueryModel(prnum=123))
    assert len(await db.find_jobs(JobQueryModel(prnum=123))) == 0

    job_branch = MurdockJob(commit, ref="refs/heads/test", user_env={"TEST": "123"})
    await db.insert_job(job_branch)
    search_job = await db.find_job(job_branch.uid)
    assert search_job is not None
    assert search_job.commit == job_branch.commit
    assert search_job.pr == job_branch.pr
    assert search_job.user_env == job_branch.user_env

    db.close()
    assert "Closing database connection" in caplog.text
