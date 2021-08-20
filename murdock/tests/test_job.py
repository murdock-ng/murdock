import os
import logging

import pytest

from ..job import MurdockJob
from ..models import FinishedJobModel, JobModel, PullRequestInfo


prinfo = PullRequestInfo(
    title="test",
    number=123,
    merge_commit="test_merge_commit",
    branch="test_branch",
    commit="test_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=True,
    labels=["test"]
)

prinfo_same_commit = PullRequestInfo(
    title="test2",
    number=124,
    merge_commit="test_merge_commit",
    branch="test_branch",
    commit="test_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=True,
    labels=["test1, test2"]
)

prinfo_different_commit = PullRequestInfo(
    title="test2",
    number=124,
    merge_commit="test_merge_commit",
    branch="test_branch",
    commit="test_different_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=True,
    labels=["test1, test2"]
)

test_job = MurdockJob(prinfo)


def test_basic(capsys):
    job = MurdockJob(prinfo)
    print(job)
    output = capsys.readouterr()
    assert output.out == "sha:test_co (PR #123)\n"

    expected_env = {
        "CI_PULL_COMMIT" : "test_commit",
        "CI_PULL_REPO" : "test/repo",
        "CI_PULL_BRANCH" : "test_branch",
        "CI_PULL_NR" : "123",
        "CI_PULL_URL" : "test_url",
        "CI_PULL_TITLE" : "test",
        "CI_PULL_USER" : "test_user",
        "CI_BASE_REPO" : "test_base_repo",
        "CI_BASE_BRANCH" : "test_base_branch",
        "CI_BASE_COMMIT" : "test_base_commit",
        "CI_SCRIPTS_DIR" : "/tmp",
        "CI_PULL_LABELS" : "test",
        "CI_BUILD_HTTP_ROOT" : f"https://ci.riot-os.org/test/repo/123/test_commit/{job.start_time}",
        "CI_BASE_URL": "https://ci.riot-os.org",
        "CI_API_TOKEN": job.token,
        "CI_MERGE_COMMIT": "test_merge_commit"
    }

    assert job.env == expected_env


def test_create_dir(tmpdir, caplog):
    caplog.set_level(logging.DEBUG, logger="murdock")
    new_dir = tmpdir.join("new").realpath()
    MurdockJob.create_dir(new_dir)
    assert os.path.exists(new_dir)
    assert f"Creating directory '{new_dir}'" in caplog.text
    MurdockJob.create_dir(new_dir)
    assert f"Directory '{new_dir}' already exists, recreate" in caplog.text


def test_remove_dir(tmpdir, caplog):
    caplog.set_level(logging.DEBUG, logger="murdock")
    dir_to_remove = tmpdir.join("remove").realpath()
    MurdockJob.create_dir(dir_to_remove)
    assert os.path.exists(dir_to_remove)
    MurdockJob.remove_dir(dir_to_remove)
    assert f"Removing directory '{dir_to_remove}'" in caplog.text
    assert (
        f"Directory '{dir_to_remove}' doesn't exist, cannot remove"
    ) not in caplog.text
    MurdockJob.remove_dir(dir_to_remove)
    assert (
        f"Directory '{dir_to_remove}' doesn't exist, cannot remove"
    ) in caplog.text


@pytest.mark.parametrize(
    "runtime,expected", [
        (30, "30s"),
        (112, "01m:52s"),
        (3662, "01h:01m:02s"),
        (100000, "02d:03h:46m:40s")
    ]
)
def test_runtime(runtime, expected):
    job = MurdockJob(prinfo)
    job.stop_time = job.start_time + runtime
    assert job.runtime_human == expected


@pytest.mark.parametrize(
    "job,other,expected", [
        (test_job, test_job, True),
        (MurdockJob(prinfo), MurdockJob(prinfo), True),
        (MurdockJob(prinfo), MurdockJob(prinfo_same_commit), True),
        (MurdockJob(prinfo), None, False),
        (MurdockJob(prinfo), MurdockJob(prinfo_different_commit), False),
    ]
)
def test_job_equality(job, other, expected):
    assert (job == other) is expected


def test_queued_model():
    job = MurdockJob(prinfo)
    expected_model = JobModel(
        uid=job.uid, prinfo=prinfo, since=job.start_time, fasttracked=False
    ).dict()
    assert job.queued_model() == expected_model


def test_running_model():
    job = MurdockJob(prinfo)
    expected_model = JobModel(
        uid=job.uid, prinfo=prinfo, since=job.start_time, status=job.status
    ).dict()
    assert job.running_model() == expected_model


def test_to_db_entry():
    job = MurdockJob(prinfo)
    job.result = "passed"
    expected_model = FinishedJobModel(
        uid=job.uid,
        since= job.start_time,
        runtime=job.runtime,
        result="passed",
        output_url=job.output_url,
        work_dir=job.work_dir,
        status=job.status,
        prinfo=job.pr.dict(),
    ).dict()
    assert MurdockJob.to_db_entry(job) == expected_model


def test_from_db_entry():
    entry = {
        "uid": "123",
        "since": 12345,
        "runtime": 1234.5,
        "result": "passed",
        "output_url": "output.html",
        "work_dir": "/tmp",
        "status": {"status": "test"},
        "prinfo": prinfo.dict(),
    }
    result = MurdockJob.from_db_entry(entry)
    assert result == FinishedJobModel(
        uid="123",
        since=12345,
        runtime=1234.5,
        result="passed",
        output_url="output.html",
        status={"status": "test"},
        prinfo=prinfo.dict(),
    ).dict()
