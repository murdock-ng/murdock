import json
import logging
from unittest import mock

import pytest

from httpx import Response

from ..job import MurdockJob
from ..models import CommitModel, PullRequestInfo
from ..murdock import Murdock
from ..notify import Notifier


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
@pytest.mark.parametrize(
    "job,previous_state,new_state,matrix,mail",
    [
        pytest.param(
            MurdockJob(commit, pr=prinfo), "passed", "passed", True, False, id="pr_pp"
        ),
        pytest.param(
            MurdockJob(commit, pr=prinfo), "passed", "errored", True, False, id="pr_pe"
        ),
        pytest.param(
            MurdockJob(commit, pr=prinfo), "errored", "errored", True, False, id="pr_ee"
        ),
        pytest.param(
            MurdockJob(commit, pr=prinfo), "errored", "passed", True, False, id="pr_ep"
        ),
        pytest.param(
            MurdockJob(commit, ref="Commit 123"),
            "passed",
            "passed",
            True,
            False,
            id="commit_pp",
        ),
        pytest.param(
            MurdockJob(commit, ref="Commit 123"),
            "passed",
            "errored",
            True,
            False,
            id="commit_pe",
        ),
        pytest.param(
            MurdockJob(commit, ref="Commit 123"),
            "errored",
            "errored",
            True,
            False,
            id="commit_ee",
        ),
        pytest.param(
            MurdockJob(commit, ref="Commit 123"),
            "errored",
            "passed",
            True,
            False,
            id="commit_ep",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/heads/test"),
            "passed",
            "passed",
            False,
            False,
            id="branch_pp",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/heads/test"),
            "passed",
            "errored",
            True,
            True,
            id="branch_pe",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/heads/test"),
            "errored",
            "errored",
            True,
            True,
            id="branch_ee",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/heads/test"),
            "errored",
            "passed",
            True,
            True,
            id="branch_ep",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/tags/test"),
            "passed",
            "passed",
            False,
            False,
            id="tag_pp",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/tags/test"),
            "passed",
            "errored",
            True,
            True,
            id="tag_pe",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/tags/test"),
            "errored",
            "errored",
            True,
            True,
            id="tag_ee",
        ),
        pytest.param(
            MurdockJob(commit, ref="refs/tags/test"),
            "errored",
            "passed",
            True,
            True,
            id="tag_ep",
        ),
    ],
)
@mock.patch("httpx.AsyncClient.post")
@mock.patch("aiosmtplib.send")
async def test_notify(
    mail_send, matrix_post, job, previous_state, new_state, matrix, mail, caplog
):
    caplog.set_level(logging.DEBUG, logger="murdock")
    matrix_post.return_value = Response(200, text=json.dumps({"details": "ok"}))
    murdock = Murdock()
    await murdock.init()
    notifier = Notifier()
    job.state = previous_state
    await murdock.db.insert_job(job)
    search_job = await murdock.db.find_job(job.uid)
    assert search_job is not None and search_job.commit == job.commit
    job.state = new_state
    await notifier.notify(job, murdock.db)
    if matrix is True:
        matrix_post.assert_called_once()
        assert "Notification posted on Matrix room " in caplog.text
    else:
        matrix_post.assert_not_called()
        assert "Notification posted on Matrix room " not in caplog.text
    if mail is True:
        mail_send.assert_called_once()
    else:
        mail_send.assert_not_called()
