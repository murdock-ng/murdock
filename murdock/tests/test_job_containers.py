import pytest

from murdock.models import CommitModel, JobQueryModel, PullRequestInfo
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


test_job1 = MurdockJob(
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
        labels=["test"],
    ),
)
test_job2 = MurdockJob(
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
        state="open",
        labels=["test"],
    ),
)
test_job3 = MurdockJob(
    CommitModel(sha="2", message="job3", author="test"),
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
        state="closed",
        labels=["test"],
    ),
)
test_job4 = MurdockJob(
    CommitModel(sha="3", message="job4", author="other_test"), ref="test_ref"
)
test_job5 = MurdockJob(
    CommitModel(sha="4", message="job5", author="other_test"), ref="test_ref"
)
test_job6 = MurdockJob(
    CommitModel(sha="3", message="job6", author="other_test"),
    ref="refs/heads/test_branch",
)
test_job7 = MurdockJob(
    CommitModel(sha="4", message="job7", author="test"), ref="refs/tags/test_tag"
)


@pytest.mark.parametrize(
    "prnum,result",
    [
        (123, [test_job2, test_job1]),
        (1234, [test_job3]),
        (12, []),
    ],
)
def test_list_search_by_pr_number(prnum, result):
    job_list = MurdockJobList()
    job_list.add(*[test_job1, test_job2, test_job3])
    assert job_list.search_by_pr_number(prnum) == result


@pytest.mark.parametrize(
    "ref,result",
    [
        ("test_ref", [test_job5, test_job4]),
        ("unknown_ref", []),
    ],
)
def test_list_search_by_ref(ref, result):
    job_list = MurdockJobList()
    job_list.add(*[test_job1, test_job2, test_job3, test_job4, test_job5])
    assert job_list.search_by_ref(ref) == result


@pytest.mark.parametrize(
    "job,result",
    [
        (test_job1, [test_job2, test_job1]),
        (test_job3, [test_job3]),
        (test_job4, [test_job5, test_job4]),
    ],
)
def test_list_search_matching(job, result):
    job_list = MurdockJobList()
    job_list.add(
        *[
            test_job1,
            test_job2,
            test_job3,
            test_job4,
            test_job5,
            test_job6,
            test_job7,
        ]
    )
    assert job_list.search_matching(job) == result


@pytest.mark.parametrize(
    "query,result",
    [
        ({"uid": test_job1.uid}, [test_job1]),
        ({"uid": "unknown_uid"}, []),
        ({"is_pr": True}, [test_job3, test_job2, test_job1]),
        ({"is_pr": False}, [test_job7, test_job6, test_job5, test_job4]),
        ({"is_branch": True}, [test_job6]),
        (
            {"is_branch": False},
            [test_job7, test_job5, test_job4, test_job3, test_job2, test_job1],
        ),
        ({"is_tag": True}, [test_job7]),
        (
            {"is_tag": False},
            [test_job6, test_job5, test_job4, test_job3, test_job2, test_job1],
        ),
        ({"is_pr": True, "is_branch": True, "is_tag": True}, []),
        ({"is_pr": False, "is_branch": False, "is_tag": False}, [test_job5, test_job4]),
        ({"prnum": 123}, [test_job2, test_job1]),
        ({"prnum": 1234}, [test_job3]),
        ({"prnum": 12}, []),
        ({"ref": "test_ref"}, [test_job5, test_job4]),
        ({"ref": "uknown_ref"}, []),
        ({"branch": "test_branch"}, [test_job6]),
        ({"branch": "unknown_branch"}, []),
        ({"tag": "test_tag"}, [test_job7]),
        ({"tag": "unknown_tag"}, []),
        ({"sha": "2"}, [test_job3, test_job2]),
        ({"sha": "4"}, [test_job7, test_job5]),
        ({"sha": "42"}, []),
        ({"author": "test"}, [test_job7, test_job3, test_job2, test_job1]),
        ({"author": "unknown"}, []),
        (
            {"result": "unknown"},
            [
                test_job7,
                test_job6,
                test_job5,
                test_job4,
                test_job3,
                test_job2,
                test_job1,
            ],
        ),
        (
            {
                "is_pr": True,
                "prnum": 123,
                "sha": "2",
                "author": "test",
            },
            [test_job2],
        ),
        ({"is_pr": True, "prstates": "open"}, [test_job2]),
        ({"is_pr": True, "prstates": "closed"}, [test_job3]),
        ({"is_pr": True, "prstates": "open closed"}, [test_job3, test_job2]),
    ],
)
def test_list_search_with_query(query, result):
    job_list = MurdockJobList()
    job_list.add(
        *[test_job1, test_job2, test_job3, test_job4, test_job5, test_job6, test_job7]
    )

    found = job_list.search_with_query(JobQueryModel(**query))

    assert found == result


def test_list_search_with_query_on_empty_list():
    job_list = MurdockJobList()
    assert job_list.jobs == []

    found = job_list.search_with_query(JobQueryModel())

    assert found == []


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
