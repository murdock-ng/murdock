import asyncio
import json
import logging
import os
from murdock.job import MurdockJob
from unittest import mock

import pytest

from murdock.murdock import Murdock
from murdock.models import CommitModel, PullRequestInfo
from murdock.config import CI_CONFIG, GLOBAL_CONFIG, MurdockSettings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected", [
        ({"num_workers": 1}, 1),
        ({"num_workers": 2}, 2),
        ({"num_workers": 3}, 3),
        ({"num_workers": 4}, 4),
        ({}, GLOBAL_CONFIG.num_workers),
    ]
)
@mock.patch("murdock.database.Database.init")
@mock.patch("murdock.murdock.Murdock.job_processing_task")
async def test_init(task, db_init, params, expected):
    murdock = Murdock(**params)
    await murdock.init()
    db_init.assert_called_once()
    assert task.call_count == expected


@pytest.mark.asyncio
@mock.patch("murdock.database.Database.close")
async def test_shutdown(db_close, caplog):
    murdock = Murdock()
    await murdock.shutdown()
    assert "Shutting down Murdock" in caplog.text
    db_close.assert_called_once()


TEST_SCRIPT = """#!/bin/bash
ACTION="$1"

case "$ACTION" in
    build)
        echo "BUILD"
        sleep 1
        exit {build_ret}
        ;;
    post_build)
        sleep 1
        exit {post_build_ret}
        ;;
    *)
        echo "$0: unhandled action $ACTION"
        exit 1
        ;;
esac
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ret,job_result,post_build_stop,comment_on_pr", [
        pytest.param(
            {"build_ret": 0, "post_build_ret": 0}, "passed", False, True,
            id="job_succeeded"
        ),
        pytest.param(
           {"build_ret": 1, "post_build_ret": 0}, "errored", False, True,
           id="job_failed"
        ),
        pytest.param(
           {"build_ret": 0, "post_build_ret": 1}, "errored", False, False,
           id="job_post_build_failed"
        ),
        pytest.param(
            {"build_ret": 0, "post_build_ret": 0}, "stopped", False, False,
            id="job_stopped"
        ),
        pytest.param(
            {"build_ret": 0, "post_build_ret": 0}, "stopped", True, False,
            id="job_post_build_stopped"
        ),
    ]
)
@mock.patch("murdock.murdock.comment_on_pr")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.database.Database.insert_job")
async def test_schedule_single_job(
    insert, status, comment, ret, job_result, post_build_stop, comment_on_pr,
    tmpdir
):
    commit = CommitModel(
        sha="test_commit", message="test message", author="test_user"
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
        labels=["test"]
    )
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "build.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(**ret))
    os.chmod(script_file, 0o744)
    work_dir = tmpdir.join("result").realpath()
    job = MurdockJob(
        commit, pr=prinfo,
        config=MurdockSettings(pr={"enable_comments": comment_on_pr})
    )
    job.scripts_dir = scripts_dir
    job.work_dir = work_dir
    murdock = Murdock()
    await murdock.init()
    await murdock.schedule_job(job)
    assert job in murdock.queued.jobs
    assert job not in murdock.active.jobs
    await asyncio.sleep(1)
    assert job not in murdock.queued.jobs
    assert job in murdock.active.jobs
    job.status = {"status": "working"}
    if job_result == "stopped":
        if post_build_stop is True:
            await asyncio.sleep(1)
        await murdock.stop_active_job(job)
        await asyncio.sleep(0.1)
        assert job not in murdock.active.jobs
    else:
        await asyncio.sleep(2)
        insert.assert_called_with(job)
        assert "BUILD" in job.output
    if comment_on_pr is True:
        comment.assert_called_once()
    else:
        comment.assert_not_called()
    assert job.status == {"status": "finished"}
    assert job.result == job_result
    assert status.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prnums,num_queued,free_slots", [
        pytest.param([1, 2, 3, 4], 4, 0, id="queued_all_different"),
        pytest.param([1, 2, 1, 2], 2, 2, id="queued_some_matching"),
    ]
)
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.database.Database.insert_job")
async def test_schedule_multiple_jobs(
    _, __, prnums, num_queued, free_slots, tmpdir, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "build.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(build_ret=0, post_build_ret=0))
    os.chmod(script_file, 0o744)

    jobs = []
    for prnum in prnums:
        commit = CommitModel(
            sha="test_commit", message="test message", author="test_user"
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
            labels=["test"]
        )
        job = MurdockJob(commit, pr=prinfo)
        job.scripts_dir = scripts_dir
        work_dir = tmpdir.join("result", job.uid).realpath()
        job.work_dir = work_dir
        jobs.append(job)

    murdock = Murdock()
    await murdock.init()
    for job in jobs:
        await murdock.schedule_job(job)

    assert len(murdock.queued.jobs) == num_queued
    await asyncio.sleep(1)
    assert len(murdock.active.jobs) == 2
    await asyncio.sleep(2.5)
    assert murdock.active.jobs.count(None) == free_slots
    await asyncio.sleep(1)
    assert murdock.active.jobs.count(None) == 2


@pytest.mark.asyncio
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.database.Database.insert_job")
async def test_schedule_multiple_jobs_with_fasttracked(_, __, tmpdir, caplog):
    caplog.set_level(logging.DEBUG, logger="murdock")
    scripts_dir = tmpdir.join("scripts").realpath()
    os.makedirs(scripts_dir)
    script_file = os.path.join(scripts_dir, "build.sh")
    with open(script_file, "w") as f:
        f.write(TEST_SCRIPT.format(build_ret=0, post_build_ret=0))
    os.chmod(script_file, 0o744)

    jobs = []
    num_jobs = 3
    for prnum in range(1, num_jobs + 1):
        commit = CommitModel(
            sha="test_commit", message="test message", author="test_user"
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
            labels=["test"]
        )
        job = MurdockJob(commit, pr=prinfo)
        if prnum == num_jobs:
            job.fasttracked = True
        job.scripts_dir = scripts_dir
        work_dir = tmpdir.join("result", job.uid).realpath()
        job.work_dir = work_dir
        jobs.append(job)

    murdock = Murdock(num_workers=1)
    await murdock.init()
    for job in jobs[:num_jobs - 1]:
        await murdock.schedule_job(job)

    import json
    await asyncio.sleep(1)
    assert len(murdock.queued.jobs) == num_jobs - 2
    assert murdock.active.jobs[0] in jobs[:num_jobs - 1]
    await asyncio.sleep(0.1)
    await murdock.schedule_job(jobs[-1])
    assert murdock.get_queued_jobs()[-1].uid == jobs[-1].uid
    await asyncio.sleep(1.5)
    assert murdock.active.jobs[0] == jobs[-1]
    assert murdock.get_active_jobs()[-1].uid == jobs[-1].uid
    await asyncio.sleep(2)
    assert jobs[-1].result == "passed"
    assert murdock.active.jobs[0] in jobs[:num_jobs - 1]
    await asyncio.sleep(2.1)
    assert len(murdock.queued.jobs) == 0
    assert murdock.active.jobs[0] == None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_found", [
        None, MurdockJob(CommitModel(
            sha="test_commit", message="test message", author="test_user"
        ))
    ]
)
@mock.patch("murdock.murdock.Murdock.schedule_job")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.database.Database.find_job")
async def test_restart_job(find, fetch_config, schedule, job_found, caplog):
    murdock = Murdock()
    find.return_value = job_found
    await murdock.restart_job("1234")
    if job_found is None:
        schedule.assert_not_called()
    else:
        fetch_config.assert_called_once()
        schedule.assert_called_once()
        schedule.call_args[0][0].commit == job_found.commit
        assert f"Restarting job {job_found}" in caplog.text


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
    },
    "label": {"name": "test label"},
}


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_action_missing(
    queued, fetch_commit, fetch_config, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_not_called()
    fetch_commit.assert_not_called()
    assert f"Handle pull request event" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,allowed,queued_called", [
        ("invalid", False, False),
        ("synchronize", True, True),
        ("labeled", True, False),
        ("unlabeled", True, True),
        ("opened", True, True),
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
    queued, disable, fetch_commit, fetch_config,
    action, allowed, queued_called, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": action})
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
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
        assert f"PR #123 closed, disabling matching jobs" in caplog.text
        disable.assert_called_once()
    else:
        assert f"PR #123 closed, disabling matching jobs" not in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_missing_commit_info(
    queued, fetch_commit, fetch_config, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": "synchronize"})
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = None
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_not_called()
    fetch_commit.assert_called_once()
    assert f"Handle pull request event" in caplog.text
    assert "Cannot fetch commit information, aborting" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_message,keywords,skipped", [
        pytest.param(
            "test message", ["ci: skip", "ci: ignore"], False,
            id="no_skip"
        ),
        pytest.param(
            "ci: skip", ["ci: skip", "ci: ignore"], True,
            id="skip"
        ),
        pytest.param(
            "message summary\n\nci: skip", ["ci: skip"], True,
            id="skip_multi_lines"
        ),
        pytest.param(
            "message summary\n\ndetail", ["ci: skip"], False,
            id="no_skip_multi_lines"
        ),
    ]
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_skip_commit(
    queued, commit_status, fetch_commit, fetch_config,
    commit_message, keywords, skipped, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    event = pr_event.copy()
    event.update({"action": "synchronize"})
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(
        commit={"skip_keywords": keywords}
    )
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message=commit_message,
        author="me"
    )
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
    assert f"Handle pull request event" in caplog.text
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    if skipped:
        queued.assert_not_called()
        commit_status.assert_called_once()
        assert "Commit message contains skip keywords, skipping job" in caplog.text
        assert f"Scheduling new job sha:{commit} (PR #123)" not in caplog.text
    else:
        queued.assert_called_once()
        commit_status.assert_not_called()
        assert "Commit message contains skip keywords, skipping job" not in caplog.text
        assert f"Scheduling new job sha:{commit} (PR #123)" in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_missing_ready_label(
    queued, fetch_commit, fetch_config, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    commit = "abcdef"
    event = pr_event.copy()
    event["pull_request"]["labels"] = []
    event.update({"action": "synchronize"})
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test message",
        author="me"
    )
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
    queued.assert_not_called()
    fetch_config.assert_called_once()
    fetch_commit.assert_called_once()
    assert f"Handle pull request event" in caplog.text
    assert f"'{CI_CONFIG.ready_label}' label not set" in caplog.text
    assert f"Scheduling new job sha:{commit} (PR #123)" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,labels,param_queued,scheduled", [
        pytest.param(
            "random label", [], [], False,
            id="ready_label_not_set"
        ),
        pytest.param(
            "random label", [{"name": CI_CONFIG.ready_label}],
            [
                MurdockJob(
                    CommitModel(
                        sha="abcdef",
                        message="test",
                        author="me"
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
                        labels=["test"]
                    )
                )
            ], False,
            id="random_label_set_already_queued"
        ),
        pytest.param(
            CI_CONFIG.ready_label, [{"name": CI_CONFIG.ready_label}],
            [
                MurdockJob(
                    CommitModel(
                        sha="abcdef",
                        message="test",
                        author="me"
                    ),
                ),
            ], True,
            id="ready_label_set"
        ),
        pytest.param(
            CI_CONFIG.ready_label, [{"name": CI_CONFIG.ready_label}], [], True,
            id="ready_label_set_already_not_queued"
        ),
    ]
)
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_pr_number")
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_labeled_action(
    queued, fetch_commit, fetch_config, pr_queued,
    label, labels, param_queued, scheduled,
    caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    commit = "abcdef"
    event = pr_event.copy()
    event.update({"action": "labeled"})
    event.update({"label": {"name": label}})
    event["pull_request"]["labels"] = labels
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test message",
        author="me"
    )
    pr_queued.return_value = param_queued
    murdock = Murdock()
    await murdock.handle_pull_request_event(event)
    assert f"Handle pull request event" in caplog.text
    fetch_config.assert_called_once()
    fetch_commit.assert_called_once()
    if scheduled:
        queued.assert_called_once()
        assert f"Scheduling new job sha:{commit} (PR #123)" in caplog.text
    else:
        queued.assert_not_called()
        assert f"Scheduling new job sha:{commit} (PR #123)" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref,ref_type,ref_name", [
        pytest.param(
            "refs/heads/test_branch", "branches", "test_branch",
            id="branches"
        ),
        pytest.param(
            "refs/tags/test_tags", "tags", "test_tags",
            id="tags"
        ),
    ]
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event(
    queued, fetch_commit, fetch_config, ref, ref_type, ref_name, caplog
):
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(
        push={ref_type: [ref_name]}
    )
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    murdock = Murdock()
    event = {"ref": ref, "after": commit}
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    queued.assert_called_once()
    assert f"Handle push event on ref '{ref_name}'" in caplog.text
    assert f"Scheduling new job sha:{commit} ({ref})" in caplog.text
    assert "Cannot fetch commit information, aborting" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref,ref_name,settings", [
        pytest.param(
            "refs/heads/test_branch", "test_branch",
            MurdockSettings(push={"branches": ["other_branch"]}),
            id="branches"
        ),
        pytest.param(
            "refs/tags/test_tags", "test_tags",
            MurdockSettings(push={"tags": ["other_tags"]}),
            id="tags"
        ),
    ]
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_ref_not_handled(
    queued, fetch_commit, fetch_config, ref, ref_name, settings, caplog
):
    commit = "abcdef"
    fetch_config.return_value = settings
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    murdock = Murdock()
    event = {"ref": ref, "after": commit}
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    queued.assert_not_called()
    assert f"Ref '{ref_name}' not accepted for push events" not in caplog.text
    assert f"Handle push event on ref '{ref_name}'" not in caplog.text
    assert f"Scheduling new job sha:{commit} ({ref})" not in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_commit_fetch_error(
    queued, fetch_commit, fetch_config, caplog
):
    branch = "test_branch"
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = None
    murdock = Murdock()
    event = {
        "ref": f"refs/heads/{branch}",
        "after": commit
    }
    await murdock.handle_push_event(event)
    fetch_commit.assert_called_with(commit)
    fetch_config.assert_not_called()
    queued.assert_not_called()
    assert f"Handle push event on ref '{branch}'" not in caplog.text
    assert f"Scheduling new job sha:{commit} (refs/heads/{branch})" not in caplog.text
    assert "Cannot fetch commit information, aborting" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_message,keywords,skipped", [
        pytest.param(
            "test message", ["ci: skip", "ci: ignore"], False,
            id="no_skip"
        ),
        pytest.param(
            "ci: skip", ["ci: skip", "ci: ignore"], True,
            id="skip"
        ),
        pytest.param(
            "message summary\n\nci: skip", ["ci: skip"], True,
            id="skip_multi_lines"
        ),
        pytest.param(
            "message summary\n\ndetail", ["ci: skip"], False,
            id="no_skip_multi_lines"
        ),
    ]
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.set_commit_status")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_push_event_skip_commit(
    queued, commit_status, fetch_commit, fetch_config,
    commit_message, keywords, skipped, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    branch = "test_branch"
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings(
        push={"branches": [branch]},
        commit={"skip_keywords": keywords}
    )
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message=commit_message,
        author="me"
    )
    murdock = Murdock()
    event = {"ref": f"refs/heads/{branch}", "after": commit}
    await murdock.handle_push_event(event)
    fetch_config.assert_called_with(commit)
    fetch_commit.assert_called_with(commit)
    assert f"Ref '{branch}' not accepted for push events" not in caplog.text
    if skipped is False:
        commit_status.assert_not_called()
        queued.assert_called_once()
        assert f"Handle push event on ref '{branch}'" in caplog.text
        assert f"Scheduling new job sha:{commit} (refs/heads/{branch})" in caplog.text
    else:
        queued.assert_not_called()
        commit_status.assert_called_once()
        assert f"Handle push event on ref '{branch}'" not in caplog.text
        assert f"Scheduling new job sha:{commit} (refs/heads/{branch})" not in caplog.text
        assert f"Commit message contains skip keywords, skipping job" in caplog.text


@pytest.mark.asyncio
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job")
@mock.patch("murdock.murdock.Murdock.stop_active_job")
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_ref")
async def test_handle_push_event_ref_removed(
    search, stop, cancel, queued, fetch_commit, fetch_config, caplog
):
    branch = "test_branch"
    commit = "abcdef"
    ref = f"refs/heads/{branch}"
    commit_model = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    job = MurdockJob(commit=commit_model, ref=ref)
    search.return_value = [job]
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = commit_model
    murdock = Murdock()
    event = {
        "ref": ref,
        "before": commit,
        "after": "0000000000000000000000000000000000000000"
    }
    await murdock.handle_push_event(event)
    cancel.assert_called_with(job, reload_jobs=True)
    stop.assert_called_with(job, reload_jobs=True)
    fetch_config.assert_not_called()
    fetch_commit.assert_not_called()
    queued.assert_not_called()
    assert f"Handle push event on ref '{branch}'" not in caplog.text
    assert f"Scheduling new job sha:{commit} (refs/heads/{branch})" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job_found,data,called", [
        pytest.param(
            None, {}, False,
            id="job_not_found"
        ),
        pytest.param(
            MurdockJob(CommitModel(
                sha="test_commit", message="test message", author="test_user"
            )), {}, False,
            id="job_found_no_status"
        ),
        pytest.param(
            MurdockJob(CommitModel(
                sha="test_commit", message="test message", author="test_user"
            )), {"status": ""}, False,
            id="job_found_empty_status"
        ),
        pytest.param(
            MurdockJob(CommitModel(
                sha="test_commit", message="test message", author="test_user"
            )), {"status": "test"}, True,
            id="job_found_valid_status"
        ),
    ]
)
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
@mock.patch("murdock.murdock.Murdock._broadcast_message")
async def test_handle_job_status_data(
    broadcast, search, job_found, data, called
):
    search.return_value = job_found
    murdock = Murdock()
    await murdock.handle_job_status_data("1234", data)
    if called is True:
        data.update({"cmd": "status", "uid": job_found.uid})
        broadcast.assert_called_with(json.dumps(data))
    else:
        broadcast.assert_not_called()
