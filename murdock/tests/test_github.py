import json
from unittest import mock

import pytest

from httpx import Response

from murdock.config import GITHUB_CONFIG, MurdockSettings
from murdock.models import CommitModel
from murdock.github import (
    comment_on_pr, fetch_commit_info, set_commit_status,
    fetch_murdock_config
)


@pytest.mark.asyncio
@mock.patch("httpx.AsyncClient.get")
@mock.patch("httpx.AsyncClient.patch")
@mock.patch("httpx.AsyncClient.post")
async def test_comment_on_pr(post, patch, get):
    pass


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
