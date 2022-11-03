import os
import logging
import datetime
import uuid

import pytest

from ..config import MurdockSettings
from ..job import MurdockJob
from ..models import CommitModel, JobModel, PullRequestInfo


commit = CommitModel(
    sha="test_commit", tree="test_tree", message="test message", author="test_user"
)
commit_other = CommitModel(
    sha="test_commit_other",
    tree="test_other_tree",
    message="test message other",
    author="test_user",
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
prinfo_not_mergeable = PullRequestInfo(
    title="test",
    number=123,
    merge_commit="test_merge_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=False,
    labels=["test"],
)
prinfo_other = PullRequestInfo(
    title="test2",
    number=124,
    merge_commit="test_merge_commit",
    user="test_user",
    url="test_url",
    base_repo="test_base_repo",
    base_branch="test_base_branch",
    base_commit="test_base_commit",
    base_full_name="test_base_full_name",
    mergeable=True,
    labels=["test1, test2"],
)

test_job = MurdockJob(commit, pr=prinfo)


@pytest.mark.parametrize(
    "pr,ref,config,out,env",
    [
        (
            prinfo,
            None,
            MurdockSettings(),
            "job 6465798 - PR #123 (test_co)\n",
            {
                "CI_PULL_COMMIT": "test_commit",
                "CI_PULL_REPO": "test/repo",
                "CI_PULL_NR": "123",
                "CI_PULL_URL": "test_url",
                "CI_PULL_USER": "test_user",
                "CI_BASE_REPO": "test_base_repo",
                "CI_BASE_BRANCH": "test_base_branch",
                "CI_BASE_COMMIT": "test_base_commit",
                "CI_PULL_LABELS": "test",
                "CI_MERGE_COMMIT": "test_merge_commit",
            },
        ),
        (
            prinfo,
            None,
            MurdockSettings(env={"TEST_ENV": "42"}),
            "job 6465798 - PR #123 (test_co)\n",
            {
                "CI_PULL_COMMIT": "test_commit",
                "CI_PULL_REPO": "test/repo",
                "CI_PULL_NR": "123",
                "CI_PULL_URL": "test_url",
                "CI_PULL_USER": "test_user",
                "CI_BASE_REPO": "test_base_repo",
                "CI_BASE_BRANCH": "test_base_branch",
                "CI_BASE_COMMIT": "test_base_commit",
                "CI_PULL_LABELS": "test",
                "CI_MERGE_COMMIT": "test_merge_commit",
                "TEST_ENV": "42",
            },
        ),
        (
            prinfo_not_mergeable,
            None,
            MurdockSettings(),
            "job 6465798 - PR #123 (test_co)\n",
            {
                "CI_PULL_COMMIT": "test_commit",
                "CI_PULL_REPO": "test/repo",
                "CI_PULL_NR": "123",
                "CI_PULL_URL": "test_url",
                "CI_PULL_USER": "test_user",
                "CI_BASE_REPO": "test_base_repo",
                "CI_BASE_BRANCH": "test_base_branch",
                "CI_BASE_COMMIT": "test_base_commit",
                "CI_PULL_LABELS": "test",
            },
        ),
        (
            None,
            "refs/heads/test_branch",
            MurdockSettings(),
            "job 6465798 - branch test_branch (test_co)\n",
            {
                "CI_BUILD_COMMIT": "test_commit",
                "CI_BUILD_REF": "refs/heads/test_branch",
                "CI_BUILD_BRANCH": "test_branch",
                "CI_BUILD_REPO": "test/repo",
            },
        ),
        (
            None,
            "refs/heads/test_branch",
            MurdockSettings(env={"TEST_ENV": "42"}),
            "job 6465798 - branch test_branch (test_co)\n",
            {
                "CI_BUILD_COMMIT": "test_commit",
                "CI_BUILD_REF": "refs/heads/test_branch",
                "CI_BUILD_BRANCH": "test_branch",
                "CI_BUILD_REPO": "test/repo",
                "TEST_ENV": "42",
            },
        ),
        (
            None,
            "refs/tags/test_tag",
            MurdockSettings(env={"TEST_ENV": "42"}),
            "job 6465798 - tag test_tag (test_co)\n",
            {
                "CI_BUILD_COMMIT": "test_commit",
                "CI_BUILD_REF": "refs/tags/test_tag",
                "CI_BUILD_TAG": "test_tag",
                "CI_BUILD_REPO": "test/repo",
                "TEST_ENV": "42",
            },
        ),
        (None, None, MurdockSettings(), "job 6465798 - commit test_co\n", {}),
    ],
)
def test_basic(capsys, pr, ref, config, out, env):
    job = MurdockJob(commit, pr=pr, ref=ref, config=config)
    job._uuid = uuid.UUID("6465798f3e104b0ab23adf5554647e63")
    print(job)
    output = capsys.readouterr()
    assert output.out == out

    env.update(
        {
            "CI_SCRIPTS_DIR": "/tmp",
            "CI_MURDOCK_PROJECT": "default",
            "CI_BASE_URL": "http://localhost:8000",
            "CI_BUILD_TREE": "test_tree",
            "CI_JOB_TOKEN": job.token,
            "CI_JOB_UID": job.uid,
        }
    )

    assert job.env == env


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
    assert (f"Directory '{dir_to_remove}' doesn't exist, cannot remove") in caplog.text


@pytest.mark.parametrize(
    "runtime,expected",
    [(30, "30s"), (112, "01m:52s"), (3662, "01h:01m:02s"), (100000, "02d:03h:46m:40s")],
)
def test_runtime(runtime, expected):
    job = MurdockJob(commit, pr=prinfo)
    job.set_stop_time(job.start_time + datetime.timedelta(seconds=runtime))
    assert job.runtime_human == expected


@pytest.mark.parametrize(
    "job,other,expected",
    [
        (test_job, test_job, True),
        (MurdockJob(commit, pr=prinfo), None, False),
        (MurdockJob(commit, pr=prinfo), MurdockJob(commit, pr=prinfo), False),
        (MurdockJob(commit, pr=prinfo), MurdockJob(commit, pr=prinfo_other), False),
        (
            MurdockJob(commit, pr=prinfo),
            MurdockJob(commit_other, pr=prinfo_other),
            False,
        ),
    ],
)
def test_job_equality(job, other, expected):
    assert (job == other) is expected


def test_queued_model():
    job = MurdockJob(commit, pr=prinfo)
    expected_model = JobModel(
        uid=job.uid,
        commit=commit,
        prinfo=prinfo,
        creation_time=job.creation_time.timestamp(),
        start_time=job.start_time.timestamp(),
        runtime=datetime.timedelta(seconds=0).total_seconds(),
        fasttracked=False,
        trigger="api",
        triggered_by=None,
        status={"status": ""},
        output="",
        output_text_url=None,
        env={
            "CI_BASE_BRANCH": "test_base_branch",
            "CI_BASE_COMMIT": "test_base_commit",
            "CI_BASE_REPO": "test_base_repo",
            "CI_BUILD_TREE": "test_tree",
            "CI_BASE_URL": "http://localhost:8000",
            "CI_JOB_UID": job.uid,
            "CI_MERGE_COMMIT": "test_merge_commit",
            "CI_MURDOCK_PROJECT": "default",
            "CI_PULL_COMMIT": "test_commit",
            "CI_PULL_LABELS": "test",
            "CI_PULL_NR": "123",
            "CI_PULL_REPO": "test/repo",
            "CI_PULL_URL": "test_url",
            "CI_PULL_USER": "test_user",
        },
    )
    assert job.model() == expected_model


def test_running_model():
    job = MurdockJob(commit, pr=prinfo, triggered_by="user")
    expected_model = JobModel(
        uid=job.uid,
        commit=commit,
        prinfo=prinfo,
        creation_time=job.creation_time.timestamp(),
        start_time=job.start_time.timestamp(),
        runtime=datetime.timedelta(seconds=0).total_seconds(),
        status=job.status,
        fasttracked=False,
        trigger="api",
        triggered_by="user",
        output="",
        output_text_url=None,
        env={
            "CI_BASE_BRANCH": "test_base_branch",
            "CI_BASE_COMMIT": "test_base_commit",
            "CI_BASE_REPO": "test_base_repo",
            "CI_BASE_URL": "http://localhost:8000",
            "CI_BUILD_TREE": "test_tree",
            "CI_JOB_UID": job.uid,
            "CI_MERGE_COMMIT": "test_merge_commit",
            "CI_MURDOCK_PROJECT": "default",
            "CI_PULL_COMMIT": "test_commit",
            "CI_PULL_LABELS": "test",
            "CI_PULL_NR": "123",
            "CI_PULL_REPO": "test/repo",
            "CI_PULL_URL": "test_url",
            "CI_PULL_USER": "test_user",
        },
    )
    assert job.model() == expected_model


def test_to_db_entry():
    job = MurdockJob(commit, pr=prinfo, triggered_by="user")
    job.state = "passed"
    job.output = "test output"
    expected_model = JobModel(
        uid=job.uid,
        start_time=job.start_time.timestamp(),
        creation_time=job.creation_time.timestamp(),
        runtime=job.runtime.total_seconds(),
        state="passed",
        output=job.output,
        output_text_url=job.output_text_url,
        work_dir=job.work_dir,
        status=job.status,
        prinfo=job.pr.dict(),
        commit=commit.dict(),
        fasttracked=False,
        trigger="api",
        triggered_by="user",
        env={
            "CI_BASE_BRANCH": "test_base_branch",
            "CI_BASE_COMMIT": "test_base_commit",
            "CI_BASE_REPO": "test_base_repo",
            "CI_BASE_URL": "http://localhost:8000",
            "CI_BUILD_TREE": "test_tree",
            "CI_JOB_UID": job.uid,
            "CI_MERGE_COMMIT": "test_merge_commit",
            "CI_MURDOCK_PROJECT": "default",
            "CI_PULL_COMMIT": "test_commit",
            "CI_PULL_LABELS": "test",
            "CI_PULL_NR": "123",
            "CI_PULL_REPO": "test/repo",
            "CI_PULL_URL": "test_url",
            "CI_PULL_USER": "test_user",
        },
    )
    assert MurdockJob.to_db_entry(job) == expected_model.dict(exclude_none=True)


def test_finished_model():
    entry = {
        "uid": "123",
        "creation_time": 12345,
        "start_time": 34567,
        "runtime": 1234.5,
        "state": "passed",
        "output": "job output",
        "output_text_url": "output.txt",
        "work_dir": "/tmp",
        "status": {"status": "test"},
        "prinfo": prinfo.dict(),
        "commit": commit.dict(),
    }
    result = MurdockJob.finished_model(entry)
    assert result == JobModel(
        uid="123",
        creation_time=12345,
        start_time=34567,
        runtime=1234.5,
        state="passed",
        output="job output",
        output_text_url="output.txt",
        status={"status": "test"},
        prinfo=prinfo.dict(),
        commit=commit.dict(),
    )


def test_uuid():
    job = MurdockJob(commit)
    assert job.uuid.hex == job.uid
