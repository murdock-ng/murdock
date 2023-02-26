import pytest
import random
from datetime import timedelta

from ..database import database
from ..job import MurdockJob
from ..models import CommitModel, PullRequestInfo, JobQueryModel
import uuid

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
    state="open",
)


@pytest.mark.asyncio
@pytest.mark.usefixtures("postgresql")
async def test_database_postgres(caplog):
    print("starting database test")
    prnum = random.randint(1, 60000)
    prinfo.number = prnum
    db = database("postgresql")
    await db.init()
    job_pr = MurdockJob(commit, pr=prinfo)
    await db.insert_job(job_pr)
    search_job = await db.find_job(uuid.uuid4().hex)
    assert search_job is None
    search_job = await db.find_job(job_pr.uid)
    assert search_job is not None
    assert search_job.commit == job_pr.commit
    assert search_job.pr == job_pr.pr
    assert search_job.user_env is None

    search_jobs = await db.find_jobs(JobQueryModel(prnum=600001))
    assert len(search_jobs) == 0
    assert await db.count_jobs(JobQueryModel(prnum=600001)) == 0

    assert await db.count_jobs(JobQueryModel(prnum=prnum)) == 1
    search_jobs = await db.find_jobs(JobQueryModel(prnum=prnum, prstates="open"))
    assert len(search_jobs) == 1
    assert search_jobs[0].commit == job_pr.commit
    assert search_jobs[0].prinfo == job_pr.pr

    assert await db.update_jobs(JobQueryModel(prnum=prnum), "commit.sha", "456") == 1
    search_jobs = await db.find_jobs(JobQueryModel(prnum=prnum))
    assert search_jobs[0].commit.sha == "456"

    assert (
        await db.update_jobs(JobQueryModel(prnum=prnum), "prinfo.title", "new title")
        == 1
    )
    search_jobs = await db.find_jobs(JobQueryModel(prnum=prnum))
    assert search_jobs[0].prinfo.title == "new title"

    await db.delete_jobs(JobQueryModel(prnum=prnum))
    assert len(await db.find_jobs(JobQueryModel(prnum=prnum))) == 0

    job_branch = MurdockJob(commit, ref="refs/heads/test", user_env={"TEST": "123"})
    await db.insert_job(job_branch)
    search_job = await db.find_job(job_branch.uid)
    assert search_job is not None
    assert search_job.commit == job_branch.commit
    assert search_job.pr == job_branch.pr
    assert search_job.user_env == job_branch.user_env
    assert job_branch.model() == (await db.find_jobs(JobQueryModel(is_branch=True)))[0]
    assert job_branch.model() == (await db.find_jobs(JobQueryModel(branch="test")))[0]
    time_after = job_branch.creation_time - timedelta(days=1)
    assert (
        job_branch.model()
        == (await db.find_jobs(JobQueryModel(is_tag=False, uid=job_branch.uid)))[0]
    )
    assert (
        job_branch.model()
        == (await db.find_jobs(JobQueryModel(is_pr=False, uid=job_branch.uid)))[0]
    )
    assert (
        job_branch.model()
        == (await db.find_jobs(JobQueryModel(after=time_after.strftime("%Y-%m-%d"))))[0]
    )

    await db.close()
