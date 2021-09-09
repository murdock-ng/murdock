from murdock.models import CommitModel, PullRequestInfo
from murdock.job import MurdockJob
from murdock.job_containers import MurdockJobList, MurdockJobPool


def test_list_add_remove():
    job1 = MurdockJob(CommitModel(sha="1", message="job1", author="test"))
    job2 = MurdockJob(CommitModel(sha="2", message="job2", author="test"))
    job3 = MurdockJob(CommitModel(sha="3", message="job3", author="test"))
    job_list = MurdockJobList()
    job_list.add(*[])
    assert len(job_list.jobs) == 0
    job_list.add(job1)
    assert len(job_list.jobs) == 1
    job_list.add(*[job2, job3])
    assert len(job_list.jobs) == 3
    job_list.remove(job2)
    assert len(job_list.jobs) == 2
    assert job2 not in job_list.jobs
    job_list.remove(job2)
    assert len(job_list.jobs) == 2
    assert job2 not in job_list.jobs


def test_list_search_by_uid():
    job1 = MurdockJob(CommitModel(sha="1", message="job1", author="test"))
    job2 = MurdockJob(CommitModel(sha="2", message="job2", author="test"))
    job3 = MurdockJob(CommitModel(sha="3", message="job3", author="test"))
    job_list = MurdockJobList()
    job_list.add(job1)
    assert len(job_list.jobs) == 1
    assert job_list.search_by_uid(job1.uid) is job1
    assert job_list.search_by_uid(job2.uid) is None
    job_list.add(*[job2, job3])
    assert len(job_list.jobs) == 3
    assert job_list.search_by_uid(job1.uid) is job1
    assert job_list.search_by_uid(job2.uid) is job2
    assert job_list.search_by_uid(job3.uid) is job3
    assert job_list.search_by_uid("invalid") is None


def test_list_search_by_pr_number():
    job1 = MurdockJob(
        CommitModel(sha="1", message="job1", author="test"),
        pr=PullRequestInfo(
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
            labels=["test"]
        )
    )
    job2 = MurdockJob(
        CommitModel(sha="2", message="job2", author="test"),
        pr=PullRequestInfo(
            title="test",
            number=1234,
            merge_commit="test_merge_commit",
            user="test_user",
            url="test_url",
            base_repo="test_base_repo",
            base_branch="test_base_branch",
            base_commit="test_base_commit",
            base_full_name="test_base_full_name",
            mergeable=True,
            labels=["test"]
        )
    )
    job3 = MurdockJob(
        CommitModel(sha="3", message="job3", author="test"),
        ref="test_branch"
    )
    job_list = MurdockJobList()
    job_list.add(*[job1, job2, job3])
    assert job_list.search_by_pr_number(123) == [job1]
    assert job_list.search_by_pr_number(1234) == [job2]
    assert job_list.search_by_pr_number(12) == []


def test_list_search_by_ref():
    job1 = MurdockJob(
        CommitModel(sha="1", message="job1", author="test"),
        pr=PullRequestInfo(
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
            labels=["test"]
        )
    )
    job2 = MurdockJob(
        CommitModel(sha="2", message="job2", author="test"),
        pr=PullRequestInfo(
            title="test",
            number=1234,
            merge_commit="test_merge_commit",
            user="test_user",
            url="test_url",
            base_repo="test_base_repo",
            base_branch="test_base_branch",
            base_commit="test_base_commit",
            base_full_name="test_base_full_name",
            mergeable=True,
            labels=["test"]
        )
    )
    job3 = MurdockJob(
        CommitModel(sha="3", message="job3", author="test"),
        ref="test_branch"
    )
    job_list = MurdockJobList()
    job_list.add(*[job1, job2, job3])
    assert job_list.search_by_ref("test_branch") == [job3]
    assert job_list.search_by_ref("unknown_branch") == []


def test_list_search_matching():
    job1 = MurdockJob(
        CommitModel(sha="1", message="job1", author="test"),
        pr=PullRequestInfo(
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
            labels=["test"]
        )
    )
    job2 = MurdockJob(
        CommitModel(sha="2", message="job2", author="test"),
        pr=PullRequestInfo(
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
            labels=["test"]
        )
    )
    job3 = MurdockJob(
        CommitModel(sha="3", message="job3", author="test"),
        ref="test_branch"
    )
    job4 = MurdockJob(
        CommitModel(sha="4", message="job3", author="test"),
        ref="test_branch"
    )
    job_list = MurdockJobList()
    job_list.add(*[job1, job2, job3, job4])
    assert job_list.search_matching(job1) == [job1, job2]
    assert job_list.search_matching(job3) == [job3, job4]


def test_pool_add_remove():
    job1 = MurdockJob(CommitModel(sha="1", message="job1", author="test"))
    job2 = MurdockJob(CommitModel(sha="2", message="job2", author="test"))
    job3 = MurdockJob(CommitModel(sha="3", message="job3", author="test"))
    job4 = MurdockJob(CommitModel(sha="4", message="job4", author="test"))
    job_pool = MurdockJobPool(3)
    job_pool.add(*[])
    assert len(job_pool.jobs) == 3
    assert all(job is None for job in job_pool.jobs)
    job_pool.add(job1)
    assert job1 in job_pool.jobs
    assert len(job_pool.jobs) == 3
    job_pool.add(*[job2, job3])
    assert len(job_pool.jobs) == 3
    assert job2 in job_pool.jobs
    assert job3 in job_pool.jobs
    job_pool.add(job4)
    assert job4 not in job_pool.jobs
    assert len(job_pool.jobs) == 3
    job_pool.remove(job2)
    assert len(job_pool.jobs) == 3
    assert job2 not in job_pool.jobs
    job_pool.remove(job2)
    assert len(job_pool.jobs) == 3
    assert job2 not in job_pool.jobs



def test_pool_search_by_uid():
    job1 = MurdockJob(CommitModel(sha="1", message="job1", author="test"))
    job2 = MurdockJob(CommitModel(sha="2", message="job2", author="test"))
    job3 = MurdockJob(CommitModel(sha="3", message="job3", author="test"))
    job_pool = MurdockJobPool(3)
    job_pool.add(job1)
    assert len(job_pool.jobs) == 3
    assert job_pool.search_by_uid(job1.uid) is job1
    assert job_pool.search_by_uid(job2.uid) is None
    job_pool.add(*[job2, job3])
    assert len(job_pool.jobs) == 3
    assert job_pool.search_by_uid(job1.uid) is job1
    assert job_pool.search_by_uid(job2.uid) is job2
    assert job_pool.search_by_uid(job3.uid) is job3
    assert job_pool.search_by_uid("invalid") is None
