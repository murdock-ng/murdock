import logging
from unittest import mock

import pytest

from murdock.murdock import Murdock
from murdock.models import CommitModel
from murdock.config import GLOBAL_CONFIG, MurdockSettings


@pytest.mark.asyncio
@mock.patch("murdock.database.Database.init")
@mock.patch("murdock.murdock.Murdock.job_processing_task")
async def test_init(task, db_init):
    murdock = Murdock()
    await murdock.init()
    db_init.assert_called_once()
    assert task.call_count == GLOBAL_CONFIG.num_workers


@pytest.mark.asyncio
@mock.patch("murdock.database.Database.close")
async def test_shutdown(db_close, caplog):
    murdock = Murdock()
    await murdock.shutdown()
    assert "Shutting down Murdock" in caplog.text
    db_close.assert_called_once()


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
        ("labeled", True, True),
        ("unlabeled", True, True),
        ("opened", True, True),
        ("reopened", True, True),
        ("closed", True, False),
        ("created", True, True),
    ],
)
@mock.patch("murdock.murdock.fetch_murdock_config")
@mock.patch("murdock.murdock.fetch_commit_info")
@mock.patch("murdock.murdock.Murdock.add_job_to_queue")
async def test_handle_pr_event_action(
    queued, fetch_commit, fetch_config, action, allowed, queued_called, caplog
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
@mock.patch("murdock.murdock.Murdock.cancel_queued_job_with_commit")
@mock.patch("murdock.murdock.Murdock.stop_active_job_with_commit")
async def test_handle_push_event_ref_removed(
    stop, cancel, queued, fetch_commit, fetch_config, caplog
):
    branch = "test_branch"
    commit = "abcdef"
    fetch_config.return_value = MurdockSettings()
    fetch_commit.return_value = CommitModel(
        sha=commit,
        message="test",
        author="me"
    )
    murdock = Murdock()
    event = {
        "ref": f"refs/heads/{branch}",
        "before": commit,
        "after": "0000000000000000000000000000000000000000"
    }
    await murdock.handle_push_event(event)
    cancel.assert_called_with(commit)
    stop.assert_called_with(commit)
    fetch_config.assert_not_called()
    fetch_commit.assert_not_called()
    queued.assert_not_called()
    assert f"Handle push event on ref '{branch}'" not in caplog.text
    assert f"Scheduling new job sha:{commit} (refs/heads/{branch})" not in caplog.text
