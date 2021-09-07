import json
from unittest import mock

import pytest

from httpx import Response

from murdock.job import MurdockJob
from murdock.config import GITHUB_CONFIG, MurdockSettings
from murdock.models import CommitModel, PullRequestInfo
from murdock.github import (
    comment_on_pr, fetch_commit_info, set_commit_status,
    fetch_murdock_config
)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job", [MurdockJob(commit, pr=prinfo), MurdockJob(commit, ref="test")]
)
@mock.patch("httpx.AsyncClient.get")
@mock.patch("httpx.AsyncClient.patch")
@mock.patch("httpx.AsyncClient.post")
async def test_comment_on_pr_disabled(post, patch, get, job):
    await comment_on_pr(job)
    post.assert_not_called()
    patch.assert_not_called()
    get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sticky,get_return,post_return,patch_return,get_called,post_called,patch_called,error",
    [
        (
            False,
            None, Response(201), None,
            False, True, False,
            None
        ),
        (
            False,
            None, Response(401, text=json.dumps({"details": "error"})), None,
            False, True, False,
            {"details": "error"}
        ),
        (
            True,
            Response(
                200,
                text=json.dumps([
                    {"body": "first comment"},
                    {"body": "second comment"},
                ])
            ), Response(201), None,
            True, True, False,
            None,
        ),
        (
            True,
            Response(
                200,
                text=json.dumps([
                    {"body": "first comment"},
                    {"body": "second comment"},
                ])
            ), Response(401, text=json.dumps({"details": "error"})), None,
            True, True, False,
            {"details": "error"}
        ),
        (
            True,
            Response(
                200,
                text=json.dumps([
                    {"body": "first comment", "id": "1"},
                    {"body": "### Murdock results", "id": "2"},
                ])
            ), None, Response(200),
            True, False, True,
            None
        ),
        (
            True,
            Response(
                200,
                text=json.dumps([
                    {"body": "first comment"},
                    {"body": "### Murdock results", "id": "2"},
                ])
            ), None, Response(401, text=json.dumps({"details": "error"})),
            True, False, True,
            {"details": "error"}
        ),
    ]
)
@mock.patch("httpx.AsyncClient.get")
@mock.patch("httpx.AsyncClient.patch")
@mock.patch("httpx.AsyncClient.post")
async def test_comment_on_pr(
    post, patch, get, sticky, get_return, post_return, patch_return,
    get_called, post_called, patch_called, error, caplog
):
    get.return_value = get_return
    post.return_value = post_return
    patch.return_value = patch_return
    job = MurdockJob(
        commit, pr=prinfo,
        config=MurdockSettings(pr={
            "enable_comments": True,
            "sticky_comment": sticky,
        })
    )
    job.result = "passed"
    comment = (
        "### Murdock results\n"
        "\n"
        f":heavy_check_mark: [PASSED](http://localhost:8000/results/{job.uid}/output.html)\n"
    )
    if sticky is True:
        comment += (
            "\n"
            "test_commit test message\n\n\n\n"
        )
    else:
        comment += "\n\n\n"

    await comment_on_pr(job)
    if get_called is True:
        get.assert_called_with(
            f"https://api.github.com/repos/test/repo/issues/123/comments",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            },
        )
    else:
        get.assert_not_called()
    if post_called is True:
        post.assert_called_with(
            f"https://api.github.com/repos/test/repo/issues/123/comments",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            },
            data=json.dumps({"body": comment})
        )
    else:
        post.assert_not_called()
    if patch_called is True:
        patch.assert_called_with(
            f"https://api.github.com/repos/test/repo/issues/comments/2",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            },
            data=json.dumps({"body": comment})
        )
    else:
        patch.assert_not_called()

    if error is not None:
        assert f"<Response [401 Unauthorized]>: {error}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,result", [
        (
            json.dumps({"details": "error"}), 403, None
        ),
        (
            json.dumps({
                "commit": {"message": "test_message"},
                "author": {"login": "me"}
            }),
            200,
            CommitModel(sha="123", message="test_message", author="me")
        ),
    ]
)
@mock.patch("httpx.AsyncClient.get")
async def test_fetch_commit_info(get, text, code, result):
    response = Response(code, text=text)
    get.return_value = response
    fetch_result = await fetch_commit_info("123")
    get.assert_called_with(
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/commits/123",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_CONFIG.api_token}"
        }
    )
    assert fetch_result == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,text,status", [
        (
            403,
            json.dumps({"details": "error"}),
            {"description": "test", "status": "test"}
        ),
        (
            201,
            json.dumps({"details": "ok"}),
            {"description": "test", "status": "test"}
        ),
    ]
)
@mock.patch("httpx.AsyncClient.post")
async def test_set_commit_status(post, caplog, code, text, status):
    post.return_value = Response(code, text=text)
    await set_commit_status("12345678", status)
    post.assert_called_with(
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/statuses/12345678",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_CONFIG.api_token}"
        },
        data=json.dumps(status)
    )
    if code == 403:
        assert f"<Response [403 Forbidden]>: {json.loads(text)}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,result", [
        (
            json.dumps({"details": "error"}), 404, {}
        ),
        (
            json.dumps({"content": ""}), 200, {}
        ),
        (
            json.dumps({"content": "YnJhbmNoZXM6IF1b"}), 200, {}
        ),
        (
            json.dumps({
                "content": (
                    "cHVzaDoKICB0YWdzOgogICAgLSAndihcZCtcLik/KFxkK1wuKT8oXCp8X"
                    "GQrKScKICBicmFuY2hlczoKICAgIC0gd2ViaG9va19wdXNoZXMKCnByOg"
                    "ogIGVuYWJsZV9jb21tZW50czogVHJ1ZQoKY29tbWl0OgogIHNraXBfa2V"
                    "5d29yZHM6IFsiY2k6IHNraXAiLCAiY2k6IG5vIiwgImNpOiBpZ25vcmUi"
                    "XQo="
                ),
            }),
            200,
            {
                'push': {'tags': ['v(\\d+\\.)?(\\d+\\.)?(\\*|\\d+)'],
                'branches': ['webhook_pushes']},
                'pr': {'enable_comments': True},
                'commit': {
                    'skip_keywords': ['ci: skip', 'ci: no', 'ci: ignore']
                }
            },
        ),
    ]
)
@mock.patch("httpx.AsyncClient.get")
async def test_fetch_murdock_config(get, text, code, result):
    response = Response(code, text=text)
    get.return_value = response
    fetch_result = await fetch_murdock_config("123")
    get.assert_called_with(
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
        "/contents/.murdock.yml?ref=123",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_CONFIG.api_token}"
        }
    )
    assert fetch_result == MurdockSettings(**result)
