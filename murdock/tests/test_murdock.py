import asyncio
import json
import logging
import os
from datetime import datetime

from fastapi import WebSocket

from murdock.murdock import Murdock
from murdock.job import MurdockJob
from unittest import mock

import pytest

from murdock.models import (
    CommitModel,
    JobQueryModel,
    PullRequestInfo,
    ManualJobBranchParamModel,
    ManualJobTagParamModel,
    ManualJobCommitParamModel,
)
from murdock.config import CI_CONFIG, GLOBAL_CONFIG, MurdockSettings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected",
    [
        ({"num_workers": 1}, 1),
        ({"num_workers": 2}, 2),
        ({"num_workers": 3}, 3),
        ({"num_workers": 4}, 4),
        ({}, GLOBAL_CONFIG.num_workers),
    ],
)
@mock.patch("murdock.database.mongodb.MongoDatabase.init")
@mock.patch("murdock.murdock.Murdock.job_processing_task")
@pytest.mark.usefixtures("clear_prometheus_registry")
async def test_init(task, db_init, params, expected):
    murdock = Murdock(**params)
    await murdock.init()
    db_init.assert_called_once()
    assert task.call_count == expected


@pytest.mark.asyncio
async def test_shutdown(caplog, murdock_mockdb):
    mock_socket = mock.Mock(spec=WebSocket)
    murdock_mockdb.add_ws_client(mock_socket)
    await murdock_mockdb.shutdown()
    assert "Shutting down Murdock" in caplog.text
    murdock_mockdb.db.close.assert_called_once()
    mock_socket.close.assert_called_once()


TEST_SCRIPT = """#!/bin/bash
echo "BUILD"
sleep 2
exit {run_ret}
"""


@pytest.mark.asyncio
@pytest.mark.usefixtures("mongo")
@pytest.mark.parametrize(
    "ret,job_state,comment_on_pr",
    [
        pytest.param(
            {"run_ret": 0},
            "passed",
            True,
            id="job_succeeded",
        ),
        pytest.param(
            {"run_ret": 1},
            "errored",
            True,
            id="job_failed",
        ),
        pytest.param(
            {"run_ret": 0},
            "stopped",
            False,
            id="job_stopped",
        ),
    ],
)
@mock.patch("murdock.murdock.comment_on_pr")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.notify.Notifier.notify")
@pytest.mark.murdock_args({"enable_notifications": True})
async def test_schedule_single_job(
    notify, status, comment, ret, job_state, comment_on_pr, tmpdir, murdock_mockdb
):
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
        is_merged=False,
    )
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "run.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(**ret))
    os.chmod(script_file, 0o744)
    work_dir = tmpdir.join("result").realpath()
    job = MurdockJob(
        commit, pr=prinfo, config=MurdockSettings(pr={"enable_comments": comment_on_pr})
    )
    job.scripts_dir = scripts_dir
    job.work_dir = work_dir
    await murdock_mockdb.init()
    await murdock_mockdb.schedule_job(job)
    assert job in murdock_mockdb.queued.jobs
    assert job not in murdock_mockdb.running.jobs
    await asyncio.sleep(1)
    assert job not in murdock_mockdb.queued.jobs
    assert job in murdock_mockdb.running.jobs
    job.status = {"status": "working"}
    if job_state == "stopped":
        await murdock_mockdb.stop_running_job(job)
        await asyncio.sleep(0.1)
        assert job not in murdock_mockdb.running.jobs
        notify.assert_not_called()
    else:
        await asyncio.sleep(2)
        murdock_mockdb.db.insert_job.assert_called_with(job)
        assert "BUILD" in job.output
        notify.assert_called_once()
    if comment_on_pr is True:
        comment.assert_called_once()
    else:
        comment.assert_not_called()
    assert job.status == {"status": "finished"}
    assert job.state == job_state
    assert status.call_count == 3
    assert murdock_mockdb.job_status_counter.labels(status=job_state)._value.get() == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("mongo")
@pytest.mark.parametrize(
    "prnums,num_queued,free_slots",
    [
        pytest.param([1, 2, 3, 4], 4, 0, id="queued_all_different"),
        pytest.param([1, 2, 1, 2], 2, 2, id="queued_some_matching"),
    ],
)
@mock.patch("murdock.murdock.set_commit_status")
async def test_schedule_multiple_jobs(
    __, prnums, num_queued, free_slots, tmpdir, caplog, murdock
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "run.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(run_ret=0))
    os.chmod(script_file, 0o744)

    jobs = []
    for prnum in prnums:
        commit = CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test message",
            author="test_user",
        )
        prinfo = PullRequestInfo(
            title="test",
            number=prnum,
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
            is_merged=False,
        )
        job = MurdockJob(commit, pr=prinfo)
        job.scripts_dir = scripts_dir
        work_dir = tmpdir.join("result", job.uid).realpath()
        job.work_dir = work_dir
        jobs.append(job)

    await murdock.init()
    for job in jobs:
        await murdock.schedule_job(job)

    assert len(murdock.queued.jobs) == num_queued
    assert murdock.job_queue_counter._value.get() == len(prnums)
    await asyncio.sleep(1)
    assert len(murdock.running.jobs) == 2
    assert murdock.job_start_counter._value.get() == 2
    await asyncio.sleep(2.5)
    assert murdock.running.jobs.count(None) == free_slots
    await asyncio.sleep(1)
    assert murdock.running.jobs.count(None) == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("mongo")
@mock.patch("murdock.murdock.set_commit_status", mock.AsyncMock())
@pytest.mark.murdock_args({"num_workers": 1})
async def test_schedule_multiple_jobs_with_fasttracked(tmpdir, caplog, murdock):
    caplog.set_level(logging.DEBUG, logger="murdock")
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "run.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(run_ret=0))
    os.chmod(script_file, 0o744)

    jobs = []
    num_jobs = 3
    for prnum in range(1, num_jobs + 1):
        commit = CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test message",
            author="test_user",
        )
        prinfo = PullRequestInfo(
            title="test",
            number=prnum,
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
            is_merged=False,
        )
        job = MurdockJob(commit, pr=prinfo)
        if prnum == num_jobs:
            job.fasttracked = True
        job.scripts_dir = scripts_dir
        work_dir = tmpdir.join("result", job.uid).realpath()
        job.work_dir = work_dir
        jobs.append(job)

    await murdock.init()
    for job in jobs[: num_jobs - 1]:
        await murdock.schedule_job(job)

    await asyncio.sleep(1)
    assert len(murdock.queued.jobs) == num_jobs - 2
    assert murdock.running.jobs[0] in jobs[: num_jobs - 1]
    assert murdock.running.jobs[0].model() == (await murdock.get_jobs())[-1]
    await asyncio.sleep(0.1)
    await murdock.schedule_job(jobs[-1])
    assert murdock.get_queued_jobs()[-1].uid == jobs[-1].uid
    assert (await murdock.get_jobs())[-2].uid == jobs[-1].uid
    await asyncio.sleep(1.5)
    assert murdock.running.jobs[0] == jobs[-1]
    assert murdock.get_running_jobs()[-1].uid == jobs[-1].uid
    await asyncio.sleep(2)
    assert jobs[-1].state == "passed"
    assert murdock.running.jobs[0] in jobs[: num_jobs - 1]
    await asyncio.sleep(2.1)
    assert len(murdock.queued.jobs) == 0
    assert murdock.running.jobs[0] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_found",
    [
        None,
        MurdockJob(
            CommitModel(
                sha="test_commit",
                tree="test_tree",
                message="test message",
                author="test_user",
            )
        ),
    ],
)
@mock.patch("murdock.murdock.Murdock.schedule_job")
@mock.patch("murdock.murdock.fetch_murdock_config")
async def test_restart_job(fetch_config, schedule, job_found, caplog, murdock_mockdb):
    murdock_mockdb.db.find_job.return_value = job_found
    await murdock_mockdb.restart_job("1234", "token")
    if job_found is None:
        schedule.assert_not_called()
    else:
        fetch_config.assert_called_once()
        schedule.assert_called_once()
        schedule.call_args[0][0].commit == job_found.commit
        assert f"Restarting {job_found}" in caplog.text


pr_event = {
    "pull_request": {
        "title": "test",
        "number": 123,
        "merge_commit_sha": "abcdef",
        "head": {
            "sha": "abcdef",
            "user": {"login": "me"},
        },
        "_links": {
            "html": {"href": "http://href"},
        },
        "base": {
            "repo": {
                "clone_url": "git://clone_url",
                "full_name": "test_name",
            },
            "ref": "abcdef",
            "sha": "abcdef",
        },
        "mergeable": True,
        "labels": [{"name": "CI: ready for build"}],
        "state": "open",
        "merged_at": None,
    },
    "label": {"name": "test label"},
    "sender": {"login": "user"},
}


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_action_missing(
    queued, fetch_commit, fetch_config, caplog, murdock
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test", author="me"
    )
    await murdock.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_not_called()
    fetch_commit.assert_not_called()
    assert "Handle pull request event" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,allowed,queued_called",
    [
        ("invalid", False, False),
        ("synchronize", True, True),
        ("labeled", True, False),
        ("unlabeled", True, False),
        ("opened", True, False),
        ("reopened", True, True),
        ("closed", True, False),
        ("created", True, True),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.disable_jobs_matching")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_action(
    queued,
    disable,
    fetch_commit,
    fetch_config,
    action,
    allowed,
    queued_called,
    caplog,
    murdock_mockdb,
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": action})
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test", author="me"
    )
    murdock_mockdb.db.update_jobs.return_value = 0
    await murdock_mockdb.handle_pull_request_event(event)
    if allowed:
        fetch_config.assert_called_with(commit)
        fetch_commit.assert_called_with(commit)
        assert f"Handle pull request event '{action}'" in caplog.text
    else:
        fetch_config.assert_not_called()
        fetch_commit.assert_not_called()
        assert f"Handle pull request event '{action}'" not in caplog.text

    if queued_called:
        queued.assert_called_once()
    else:
        queued.assert_not_called()

    if action == "closed":
        assert "PR #123 closed, disabling matching jobs" in caplog.text
        disable.assert_called_once()
    else:
        assert "PR #123 closed, disabling matching jobs" not in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_missing_commit_info(
    queued, fetch_commit, fetch_config, caplog, murdock
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": "synchronize"})
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = None
    await murdock.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_not_called()
    fetch_commit.assert_called_once()
    assert "Handle pull request event" in caplog.text
    assert "Cannot fetch commit information, aborting" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_message,keywords,skipped",
    [
        pytest.param("test message", ["ci: skip", "ci: ignore"], False, id="no_skip"),
        pytest.param("ci: skip", ["ci: skip", "ci: ignore"], True, id="skip"),
        pytest.param(
            "message summary\n\nci: skip", ["ci: skip"], True, id="skip_multi_lines"
        ),
        pytest.param(
            "message summary\n\ndetail", ["ci: skip"], False, id="no_skip_multi_lines"
        ),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_skip_commit(
    queued,
    commit_status,
    fetch_commit,
    fetch_config,
    commit_message,
    keywords,
    skipped,
    caplog,
    murdock_mockdb,
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": "synchronize"})
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(commit={"skip_keywords": keywords})
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message=commit_message, author="me"
    )
    murdock_mockdb.db.update_jobs.return_value = 0
    await murdock_mockdb.handle_pull_request_event(event)
    assert "Handle pull request event" in caplog.text
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    assert "Scheduling new job" in caplog.text
    if skipped:
        queued.assert_not_called()
        commit_status.assert_called_once()
        assert "Commit message contains skip keywords, skipping job" in caplog.text
    else:
        queued.assert_called_once()
        commit_status.assert_not_called()
        assert "Commit message contains skip keywords, skipping job" not in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_missing_ready_label(
    queued, fetch_commit, fetch_config, caplog, murdock_mockdb
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    commit = "abcdef"
    event = pr_event.copy()
    event["pull_request"]["labels"] = []
    event.update({"action": "synchronize"})
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test message", author="me"
    )
    murdock_mockdb.db.update_jobs.return_value = 0
    await murdock_mockdb.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_called_once()
    fetch_commit.assert_called_once()
    assert "Handle pull request event" in caplog.text
    assert f"'{CI_CONFIG.ready_label}' label not set" in caplog.text
    assert "Scheduling new job" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels,new_label,param_queued,scheduled",
    [
        pytest.param([], "random label", [], False, id="ready_label_not_set"),
        pytest.param(
            [{"name": CI_CONFIG.ready_label}],
            CI_CONFIG.ready_label,
            [
                MurdockJob(
                    CommitModel(
                        sha="abcdef", tree="test_tree", message="test", author="me"
                    ),
                    pr=PullRequestInfo(
                        title="test",
                        number=123,
                        merge_commit="test",
                        user="test",
                        url="test",
                        base_repo="test",
                        base_branch="test",
                        base_commit="test",
                        base_full_name="test",
                        mergeable=True,
                        labels=["test"],
                        state="open",
                        is_merged=False,
                    ),
                )
            ],
            False,
            id="random_label_set_already_job_queued",
        ),
        pytest.param(
            [{"name": CI_CONFIG.ready_label}],
            CI_CONFIG.ready_label,
            [],
            True,
            id="ready_label_set_already_job_not_queued",
        ),
        pytest.param(
            [{"name": CI_CONFIG.ready_label}],
            "Random label",
            [],
            False,
            id="ready_label_unset",
        ),
    ],
)
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_pr_number")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_labeled_action(
    queued,
    fetch_commit,
    fetch_config,
    commit_status,
    pr_queued,
    labels,
    new_label,
    param_queued,
    scheduled,
    caplog,
    murdock_mockdb,
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    commit = "abcdef"
    event = pr_event.copy()
    event.update({"action": "labeled"})
    event.update({"label": {"name": new_label}})
    event["pull_request"]["labels"] = labels
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test message", author="me"
    )
    commit_status.return_value = None
    pr_queued.return_value = param_queued
    murdock_mockdb.db.update_jobs.return_value = 0
    await murdock_mockdb.handle_pull_request_event(event)
    assert "Handle pull request event" in caplog.text
    fetch_config.assert_called_once()
    fetch_commit.assert_called_once()
    if scheduled:
        queued.assert_called_once()
        assert "Scheduling new job" in caplog.text
    else:
        queued.assert_not_called()
        assert "Scheduling new job" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.murdock_args({"repository": "unsupported_repo"})
async def test_handle_pr_event_unsupported_repo(caplog, murdock):
    event = {"action": "labeled", "repository": {"full_name": "unsupported"}}
    assert await murdock.handle_pull_request_event(event) == "Invalid repo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref,ref_type,ref_name",
    [
        pytest.param(
            "refs/heads/test_branch", "branches", "test_branch", id="branches"
        ),
        pytest.param(
            "refs/heads/test_branch/with/slashes",
            "branches",
            "test_branch/with/slashes",
            id="branches_with_slashes",
        ),
        pytest.param("refs/tags/test_tags", "tags", "test_tags", id="tags"),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event(
    queued, fetch_commit, fetch_config, ref, ref_type, ref_name, caplog, murdock
):
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(push={ref_type: [ref_name]})
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test", author="me"
    )
    event = {"ref": ref, "after": commit, "sender": {"login": "user"}}
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    queued.assert_called_once()
    assert f"Handle push event on ref '{ref_name}'" in caplog.text
    assert "Scheduling new job" in caplog.text
    assert "Cannot fetch commit information, aborting" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref,ref_name,settings",
    [
        pytest.param(
            "refs/heads/test_branch",
            "test_branch",
            MurdockSettings(push={"branches": ["other_branch"]}),
            id="branches",
        ),
        pytest.param(
            "refs/heads/test_branch",
            "test_branch",
            MurdockSettings(push={"branches": []}),
            id="branches_empty_rules",
        ),
        pytest.param(
            "refs/heads/test_branch/with/slashes",
            "test_branch/with/slashes",
            MurdockSettings(push={"branches": ["other_branch"]}),
            id="branches_with_slashes",
        ),
        pytest.param(
            "refs/tags/test_tags",
            "test_tags",
            MurdockSettings(push={"tags": ["other_tags"]}),
            id="tags_empty_rules",
        ),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_ref_not_handled(
    queued, fetch_commit, fetch_config, ref, ref_name, settings, caplog, murdock
):
    commit = "abcdef"
    fetch_config.return_value = settings
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message="test", author="me"
    )
    event = {"ref": ref, "after": commit, "sender": {"login": "user"}}
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    queued.assert_not_called()
    assert f"Ref '{ref_name}' not accepted for push events" not in caplog.text
    assert f"Handle push event on ref '{ref_name}'" not in caplog.text
    assert "Scheduling new job" not in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_commit_fetch_error(
    queued, fetch_commit, fetch_config, caplog, murdock
):
    branch = "test_branch"
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = None
    event = {
        "ref": f"refs/heads/{branch}",
        "after": commit,
        "sender": {"login": "user"},
    }
    await murdock.handle_push_event(event)
    fetch_commit.assert_called_with(commit)
    fetch_config.assert_not_called()
    queued.assert_not_called()
    assert f"Handle push event on ref '{branch}'" not in caplog.text
    assert "Scheduling new job" not in caplog.text
    assert "Cannot fetch commit information, aborting" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_message,keywords,skipped",
    [
        pytest.param("test message", ["ci: skip", "ci: ignore"], False, id="no_skip"),
        pytest.param("ci: skip", ["ci: skip", "ci: ignore"], True, id="skip"),
        pytest.param(
            "message summary\n\nci: skip", ["ci: skip"], True, id="skip_multi_lines"
        ),
        pytest.param(
            "message summary\n\ndetail", ["ci: skip"], False, id="no_skip_multi_lines"
        ),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_skip_commit(
    queued,
    commit_status,
    fetch_commit,
    fetch_config,
    commit_message,
    keywords,
    skipped,
    caplog,
    murdock,
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    branch = "test_branch"
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(
        push={"branches": [branch]}, commit={"skip_keywords": keywords}
    )
    fetch_commit.return_value = CommitModel(
        sha=commit, tree="test_tree", message=commit_message, author="me"
    )
    event = {
        "ref": f"refs/heads/{branch}",
        "after": commit,
        "sender": {"login": "user"},
    }
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    assert f"Ref '{branch}' not accepted for push events" not in caplog.text
    assert f"Handle push event on ref '{branch}'" in caplog.text
    assert "Scheduling new job" in caplog.text
    if skipped is False:
        commit_status.assert_not_called()
        queued.assert_called_once()
    else:
        queued.assert_not_called()
        commit_status.assert_called_once()
        assert "Commit message contains skip keywords, skipping job" in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job")
@mock.patch("murdock.murdock.Murdock.stop_running_job")
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_ref")
async def test_handle_push_event_ref_removed(
    search, stop, cancel, queued, fetch_commit, fetch_config, caplog, murdock
):
    branch = "test_branch"
    commit = "abcdef"
    ref = f"refs/heads/{branch}"
    commit_model = CommitModel(
        sha=commit, tree="test_tree", message="test", author="me"
    )
    job = MurdockJob(commit=commit_model, ref=ref)
    search.return_value = [job]
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = commit_model
    event = {
        "ref": ref,
        "before": commit,
        "after": "0000000000000000000000000000000000000000",
        "sender": {"login": "user"},
    }
    await murdock.handle_push_event(event)
    cancel.assert_called_with(job, reload_jobs=True)
    stop.assert_called_with(job)
    fetch_config.assert_not_called()
    fetch_commit.assert_not_called()
    queued.assert_not_called()
    assert f"Handle push event on ref '{branch}'" not in caplog.text
    assert "Scheduling new job" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.murdock_args({"repository": "unsupported_repo"})
async def test_handle_push_event_unsupported_repo(caplog, murdock):
    event = {"repository": {"full_name": "unsupported"}}
    assert await murdock.handle_push_event(event) == "Invalid repo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_found,data,called",
    [
        pytest.param(None, {}, False, id="job_not_found"),
        pytest.param(
            MurdockJob(
                CommitModel(
                    sha="test_commit",
                    tree="test_tree",
                    message="test message",
                    author="test_user",
                )
            ),
            {},
            False,
            id="job_found_no_status",
        ),
        pytest.param(
            MurdockJob(
                CommitModel(
                    sha="test_commit",
                    tree="test_tree",
                    message="test message",
                    author="test_user",
                )
            ),
            {},
            False,
            id="job_found_empty_status",
        ),
        pytest.param(
            MurdockJob(
                CommitModel(
                    sha="test_commit",
                    tree="test_tree",
                    message="test message",
                    author="test_user",
                )
            ),
            {"status": "test"},
            True,
            id="job_found_valid_status",
        ),
        pytest.param(
            MurdockJob(
                CommitModel(
                    sha="test_commit",
                    tree="test_tree",
                    message="test message",
                    author="test_user",
                )
            ),
            {"status": "test", "passed": 5, "failed": 3},
            True,
            id="job_found_valid_status_w_numbers",
        ),
    ],
)
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
@mock.patch("murdock.murdock.Murdock.notify_message_to_clients")
async def test_handle_job_status_data(notify, search, job_found, data, called, murdock):
    status_data = {"status": data}
    search.return_value = job_found
    await murdock.handle_job_status_data("1234", status_data)
    if called is True:
        status_data.update({"cmd": "status", "uid": job_found.uid})
        notify.assert_called_with(json.dumps(status_data))
    else:
        notify.assert_not_called()
    if "passed" in data:
        assert (
            murdock.task_status_counter.labels("passed")._value.get() == data["passed"]
        )
    if "failed" in data:
        assert (
            murdock.task_status_counter.labels("failed")._value.get() == data["failed"]
        )


@pytest.mark.asyncio
@mock.patch("murdock.job.MurdockJob.remove_dir")
@mock.patch("murdock.murdock.Murdock.stop_running_job")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job")
async def test_remove_job(queued, running, remove_dir, murdock_mockdb):
    job_queued = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test queued message",
            author="user",
        )
    )
    job_running = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test running message",
            author="user",
        )
    )
    job_finished = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test finished message",
            author="user",
        )
    )
    murdock_mockdb.queued.add(job_queued)
    murdock_mockdb.running.add(job_running)
    murdock_mockdb.db.find_jobs.return_value = [job_finished.model()]

    job = await murdock_mockdb.remove_job(job_queued.uid)
    queued.assert_called_with(job_queued, reload_jobs=True)
    assert job_queued == job
    job = await murdock_mockdb.remove_job(job_running.uid)
    running.assert_called_with(job_running)
    assert job_running == job
    job = await murdock_mockdb.remove_job(job_finished.uid)
    remove_dir.assert_called_once()
    murdock_mockdb.db.delete_jobs.assert_called_with(
        JobQueryModel(uid=job_finished.uid)
    )
    assert job_finished == job

    murdock_mockdb.db.find_jobs.return_value = []
    job_none = await murdock_mockdb.remove_job("abcdef")
    assert job_none is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("mongo")
async def test_remove_jobs(murdock):
    await murdock.init()
    job1 = MurdockJob(
        CommitModel(
            sha="test_commit1",
            tree="test_tree",
            message="test message 1",
            author="test_user",
        )
    )
    job2 = MurdockJob(
        CommitModel(
            sha="test_commit2",
            tree="test_tree",
            message="test message 2",
            author="test_user",
        )
    )
    job1.creation_time = datetime.strptime("2022-01-01", "%Y-%m-%d")
    await murdock.db.insert_job(job1)
    await murdock.db.insert_job(job2)
    query = JobQueryModel(before="2022-06-01")
    found_jobs = await murdock.db.find_jobs(query)
    assert len(found_jobs) == 1
    deleted_jobs = await murdock.remove_finished_jobs(query)
    assert found_jobs == deleted_jobs


@pytest.mark.asyncio
async def test_get_job(murdock_mockdb):
    job_queued = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test queued message",
            author="user",
        )
    )
    job_running = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test running message",
            author="user",
        )
    )
    job_finished = MurdockJob(
        CommitModel(
            sha="test_commit",
            tree="test_tree",
            message="test finished message",
            author="user",
        )
    )
    murdock_mockdb.queued.add(job_queued)
    murdock_mockdb.running.add(job_running)
    murdock_mockdb.db.find_jobs.return_value = [job_finished.model()]
    job = await murdock_mockdb.get_job(job_queued.uid)
    assert job_queued.model() == job
    job = await murdock_mockdb.get_job(job_running.uid)
    assert job_running.model() == job
    job = await murdock_mockdb.get_job(job_finished.uid)
    assert job_finished.model() == job

    murdock_mockdb.db.find_jobs.return_value = []
    job_none = await murdock_mockdb.get_job("abcdef")
    assert job_none is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit,fasttracked,scheduled",
    [
        pytest.param(
            CommitModel(
                sha="test_sha",
                tree="test_tree",
                message="test message",
                author="test_user",
            ),
            False,
            True,
            id="regular-job",
        ),
        pytest.param(
            CommitModel(
                sha="test_sha",
                tree="test_tree",
                message="test message",
                author="test_user",
            ),
            True,
            True,
            id="fasttracked-job",
        ),
        pytest.param(None, False, False, id="no-job-matching"),
    ],
)
@mock.patch("murdock.murdock.fetch_user_login")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_branch_info")
@mock.patch("murdock.murdock.Murdock.schedule_job")
async def test_branch_manual_job(
    schedule_job,
    fetch_branch_info,
    fetch_murdock_config,
    fetch_user_login,
    commit,
    fasttracked,
    scheduled,
    murdock,
):
    fetch_branch_info.return_value = commit
    fetch_murdock_config.return_value = MurdockSettings()
    fetch_user_login.return_value = "test_login"
    job_model = await murdock.start_branch_job(
        "token", ManualJobBranchParamModel(branch="test", fasttrack=fasttracked)
    )
    if scheduled:
        schedule_job.assert_called_once()
        assert job_model.commit == commit
        assert job_model.ref == "refs/heads/test"
        assert job_model.fasttracked == fasttracked
    else:
        schedule_job.assert_not_called()
        assert job_model is None


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_user_login")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_tag_info")
@mock.patch("murdock.murdock.Murdock.schedule_job")
async def test_tag_manual_job(
    schedule_job, fetch_tag_info, fetch_murdock_config, fetch_user_login, murdock
):
    commit = CommitModel(
        sha="test_sha", tree="test_tree", message="test message", author="test_user"
    )
    fetch_tag_info.return_value = commit
    fetch_murdock_config.return_value = MurdockSettings()
    fetch_user_login.return_value = "test_login"
    job_model = await murdock.start_tag_job("token", ManualJobTagParamModel(tag="test"))
    schedule_job.assert_called_once()
    assert job_model.commit == commit
    assert job_model.ref == "refs/tags/test"


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_user_login")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.schedule_job")
async def test_commit_manual_job(
    schedule_job, fetch_commit_info, fetch_murdock_config, fetch_user_login, murdock
):
    commit = CommitModel(
        sha="test_sha", tree="test_tree", message="test message", author="test_user"
    )
    fetch_commit_info.return_value = commit
    fetch_murdock_config.return_value = MurdockSettings()
    fetch_user_login.return_value = "test_login"
    job_model = await murdock.start_commit_job(
        "token", ManualJobCommitParamModel(sha="test")
    )
    schedule_job.assert_called_once()
    assert job_model.commit == commit
    assert job_model.ref == "Commit test"
