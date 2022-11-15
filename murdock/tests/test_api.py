import os
import json
import logging

from unittest import mock

import pytest

from httpx import Response

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ..config import GITHUB_CONFIG
from ..main import app, _check_push_permissions, _check_admin_permissions
from ..job import MurdockJob
from ..models import (
    CommitModel,
    JobModel,
    JobQueryModel,
    PullRequestInfo,
)


client = TestClient(app)
commit = CommitModel(sha="test", tree="test", message="test message", author="test")
prinfo = PullRequestInfo(
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
)
test_job_queued = JobModel(
    uid="1234",
    commit=commit,
    prinfo=prinfo,
    creation_time=12345.6,
    fasttracked=True,
    state="queued",
)
test_job_running = JobModel(
    uid="1234",
    commit=commit,
    prinfo=prinfo,
    creation_time=12345.6,
    status={"status": "test"},
    state="running",
)
test_job_finished = JobModel(
    uid="1234",
    commit=commit,
    prinfo=prinfo,
    creation_time=12345.6,
    status={"status": "test"},
    state="passed",
    output_url="test",
    runtime=1234.5,
)
test_job = MurdockJob(commit, pr=prinfo)


@pytest.fixture
def push_allowed():
    async def with_push(token: str = "token"):
        return token

    app.dependency_overrides[_check_push_permissions] = with_push


@pytest.fixture
def push_not_allowed():
    async def without_push(token: str = "token"):
        raise HTTPException(status_code=401, detail="Missing push permissions")

    app.dependency_overrides[_check_push_permissions] = without_push


@pytest.fixture
def admin_allowed():
    async def with_admin(token: str = "token"):
        return token

    app.dependency_overrides[_check_admin_permissions] = with_admin


@pytest.fixture
def admin_not_allowed():
    async def without_admin(token: str = "token"):
        raise HTTPException(status_code=401, detail="Missing admin permissions")

    app.dependency_overrides[_check_admin_permissions] = without_admin


def test_openapi_exists():
    response = client.get("/api")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "event,event_type,code,msg,handle_msg",
    [
        (
            {"event": "test_data_invalid"},
            "pull_request",
            400,
            {"detail": "Invalid event signature"},
            None,
        ),
        (
            {"event": "test_data_invalid"},
            "push",
            400,
            {"detail": "Invalid event signature"},
            None,
        ),
        (
            {"event": "test_data"},
            "unsupported_event",
            400,
            {"detail": "Unsupported event"},
            None,
        ),
        (
            {"event": "test_data"},
            "pull_request",
            400,
            {"detail": "Murdock msg"},
            "Murdock msg",
        ),
        ({"event": "test_data"}, "push", 400, {"detail": "Murdock msg"}, "Murdock msg"),
        ({"event": "test_data"}, "pull_request", 200, None, None),
        ({"event": "test_data"}, "push", 200, None, None),
    ],
)
@mock.patch("murdock.murdock.Murdock.handle_pull_request_event")
@mock.patch("murdock.murdock.Murdock.handle_push_event")
def test_github_webhook(
    handle_push, handle_pr, event, event_type, code, msg, handle_msg
):
    handle_pr.return_value = handle_msg
    handle_push.return_value = handle_msg
    response = client.post(
        "/github/webhook",
        data=json.dumps(event),
        headers={
            "X-Hub-Signature-256": "sha256=c9f5bb344fb71f91afbac48f5afcd8421a3d0fbdddb3857a521e155cea75a43e",
            "X-Github-Event": event_type,
            "Content-Type": "application/vnd.github.v3+json",
        },
    )
    if event == {"event": "test_data"} and event_type == "pull_request":
        handle_pr.assert_called_with(event)
    elif event == {"event": "test_data"} and event_type == "push":
        handle_push.assert_called_with(event)
    else:
        handle_pr.assert_not_called()
        handle_push.assert_not_called()
    assert response.status_code == code
    if response.status_code != 200:
        assert response.json() == msg


@mock.patch("httpx.AsyncClient.post")
def test_github_authenticate(post):
    post.return_value = Response(200, text=json.dumps({"access_token": "test_token"}))
    response = client.get("/github/authenticate/12345")
    post.assert_called_with(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": os.getenv("MURDOCK_GITHUB_APP_CLIENT_ID"),
            "client_secret": os.getenv("MURDOCK_GITHUB_APP_CLIENT_SECRET"),
            "code": "12345",
        },
        headers={"Accept": "application/vnd.github.v3+json"},
    )
    assert response.json() == {"token": "test_token"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,valid",
    [
        ("error", 403, False),
        (json.dumps({"permissions": {"push": False}}), 200, False),
        (json.dumps({"permissions": {"push": True}}), 200, True),
    ],
)
@mock.patch("httpx.AsyncClient.get")
async def test_check_push_permissions(get, text, code, valid):
    response = Response(code, text=text)
    get.return_value = response
    if valid is False:
        with pytest.raises(HTTPException) as exc_info:
            await _check_push_permissions("token")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Missing push permissions"
    else:
        await _check_push_permissions("token")
    get.assert_called_with(
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": "token token",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,valid",
    [
        ("error", 403, False),
        (json.dumps({"permissions": {"admin": False}}), 200, False),
        (json.dumps({"permissions": {"admin": True}}), 200, True),
    ],
)
@mock.patch("httpx.AsyncClient.get")
async def test_check_admin_permissions(get, text, code, valid):
    response = Response(code, text=text)
    get.return_value = response
    if valid is False:
        with pytest.raises(HTTPException) as exc_info:
            await _check_admin_permissions("token")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Missing admin permissions"
    else:
        await _check_admin_permissions("token")
    get.assert_called_with(
        f"https://api.github.com/repos/{os.getenv('GITHUB_REPO')}",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": "token token",
        },
    )


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "job,code",
    [
        pytest.param(None, 404, id="no_job"),
        pytest.param(MurdockJob(commit, pr=prinfo).model(), 200, id="job_found"),
    ],
)
@mock.patch("murdock.murdock.Murdock.remove_job")
def test_remove_job(remove, job, code):
    remove.return_value = job
    response = client.delete("/job/abcdef")
    assert response.status_code == code
    if job is not None:
        assert response.json() == job.dict(exclude_none=True)
    else:
        assert response.json() == {"detail": "No job with uid 'abcdef' found"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.remove_job")
def test_remove_job_not_allowed(remove):
    remove.return_value = MurdockJob(commit, pr=prinfo).model()
    response = client.delete("/job/abcdef")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "job_running,job_found,headers,code,expected_response",
    [
        pytest.param(
            None,
            None,
            {},
            400,
            {"detail": "No job running with uid abcdef"},
            id="job_not_found",
        ),
        pytest.param(
            test_job,
            None,
            {},
            400,
            {"detail": "Job token is missing"},
            id="missing_job_token",
        ),
        pytest.param(
            test_job,
            None,
            {"Authorization": "invalid"},
            400,
            {"detail": "Invalid Job token"},
            id="invalid_job_token",
        ),
        pytest.param(
            test_job,
            None,
            {"Authorization": test_job.token},
            404,
            {"detail": "No job with uid 'abcdef' found"},
            id="update_job_not_found",
        ),
        pytest.param(
            test_job,
            test_job,
            {"Authorization": test_job.token},
            200,
            test_job.model().dict(exclude_none=True),
            id="update_job_found",
        ),
    ],
)
@mock.patch("murdock.murdock.Murdock.handle_job_status_data")
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
def test_update_job_status(
    running, commit_status, job_running, job_found, headers, code, expected_response
):
    running.return_value = job_running
    commit_status.return_value = job_found
    status = {"status": "test"}
    response = client.put("/job/abcdef/status", json=status, headers=headers)
    running.assert_called_with("abcdef")
    assert response.status_code == code
    if response.status_code in [200, 404]:
        commit_status.assert_called_with("abcdef", status)
    assert response.json() == expected_response


@pytest.mark.parametrize(
    "query,call_arg",
    [
        ("", JobQueryModel()),
        ("?limit=30", JobQueryModel(limit=30)),
        ("?limit=30&uid=12345", JobQueryModel(limit=30, uid="12345")),
        ("?prnum=42", JobQueryModel(prnum=42)),
        ("?branch=test", JobQueryModel(branch="test")),
        ("?sha=abcdef", JobQueryModel(sha="abcdef")),
        ("?tree=abcdef", JobQueryModel(tree="abcdef")),
        ("?author=me", JobQueryModel(author="me")),
        ("?result=passed", JobQueryModel(result="passed")),
        ("?after=after", JobQueryModel(after="after")),
        ("?before=before", JobQueryModel(before="before")),
        ("?invalid=invalid", JobQueryModel()),
    ],
)
@mock.patch("murdock.murdock.Murdock.get_queued_jobs")
@mock.patch("murdock.murdock.Murdock.get_running_jobs")
@mock.patch("murdock.database.Database.find_jobs")
def test_get_jobs_with_query(jobs, running, queued, query, call_arg):
    running.return_value = []
    queued.return_value = []
    jobs.return_value = [test_job_finished]
    client.get(f"/jobs{query}")
    jobs.assert_called_with(call_arg)


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [(None, 404), (MurdockJob(commit, pr=prinfo), 200)]
)
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job(restart, result, code):
    restart.return_value = result
    response = client.post("/job/123")
    assert response.status_code == code
    restart.assert_called_with("123", "token")
    if result is not None:
        assert response.json() == result.model().dict(exclude_none=True)
    else:
        assert response.json() == {"detail": "Cannot restart job '123'"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job_not_allowed(restart):
    restart.return_value = MurdockJob(commit, pr=prinfo)
    response = client.post("/job/123")
    restart.assert_not_called()
    assert response.status_code == 401


@pytest.mark.usefixtures("admin_allowed")
@pytest.mark.parametrize("result", [[], [test_job_finished]])
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_jobs(remove, result):
    remove.return_value = result
    before = "2021-08-16"
    response = client.delete(f"/jobs?before={before}")
    assert response.status_code == 200
    remove.assert_called_with(JobQueryModel(before=before))
    assert response.json() == [job.dict(exclude_none=True) for job in result]


@pytest.mark.usefixtures("admin_not_allowed")
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_job_not_allowed(remove):
    remove.return_value = [test_job_finished]
    before = "2021-08-16"
    response = client.delete(f"/jobs?before={before}")
    remove.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize(
    "result",
    [
        [],
        [test_job_queued.dict(exclude_none=True)],
        [
            test_job_queued.dict(exclude_none=True),
            test_job_running.dict(exclude_none=True),
        ],
        [
            test_job_queued.dict(exclude_none=True),
            test_job_running.dict(exclude_none=True),
            test_job_finished.dict(exclude_none=True),
        ],
    ],
)
@mock.patch("murdock.murdock.Murdock.get_jobs")
def test_get_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/jobs")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize(
    "result",
    [test_job_queued, test_job_running, test_job_finished],
)
@mock.patch("murdock.murdock.Murdock.get_job")
def test_get_job(get_job, result):
    get_job.return_value = result
    response = client.get("/job/12345")
    assert response.status_code == 200
    assert response.json() == result.dict(exclude_none=True)


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,data,code",
    [
        (test_job_finished, json.dumps({"branch": "test_branch"}), 200),
        (None, json.dumps({"branch": "test_branch"}), 404),
        (None, "invalid", 422),
    ],
)
@mock.patch("murdock.murdock.Murdock.start_branch_job")
def test_start_manual_branch_job(start_branch_job, result, data, code):
    start_branch_job.return_value = result
    response = client.post("/job/branch", data=data)
    assert response.status_code == code
    if result is not None:
        assert response.json() == result.dict(exclude_none=True)


@pytest.mark.parametrize(
    "result,endpoint,code",
    [
        ([test_job_finished], "branch/test", 200),
        ([], "branch/test", 404),
        ([test_job_finished], "tag/test", 200),
        ([], "tag/test", 404),
        ([test_job_finished], "commit/test", 200),
        ([], "commit/test", 404),
        ([test_job_finished], "pr/123", 200),
        ([], "pr/123", 404),
    ],
)
@mock.patch("murdock.murdock.Murdock.get_jobs")
def test_last_job(get_jobs, result, endpoint, code):
    get_jobs.return_value = result
    response = client.get(f"/job/{endpoint}")
    assert response.status_code == code
    if result:
        assert response.json() == result[0].dict(exclude_none=True)
    else:
        assert "No matching job found" in response.json()["detail"]


@pytest.mark.parametrize(
    "jobs,state,code",
    [
        ([test_job_finished], "passed", 200),
        ([test_job_finished], "errored", 200),
        ([test_job_finished], "running", 200),
        ([], "", 404),
    ],
)
@mock.patch("murdock.murdock.Murdock.get_jobs")
def test_last_branch_job_badge(get_jobs, jobs, state, code):
    if jobs:
        jobs[0].state = state
    get_jobs.return_value = jobs
    response = client.get("/job/branch/test/badge")
    assert response.status_code == code
    if code == 200:
        assert 'svg xmlns="http://www.w3.org/2000/svg"' in response.text
        assert test_job_finished.state in response.text
    else:
        assert "No matching job found" in response.json()["detail"]


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,data,code",
    [
        (test_job_finished, json.dumps({"tag": "test_tag"}), 200),
        (None, json.dumps({"tag": "test_tag"}), 404),
        (None, "invalid", 422),
    ],
)
@mock.patch("murdock.murdock.Murdock.start_tag_job")
def test_start_manual_tag_job(start_tag_job, result, data, code):
    start_tag_job.return_value = result
    response = client.post("/job/tag", data=data)
    assert response.status_code == code
    if result is not None:
        assert response.json() == result.dict(exclude_none=True)


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,data,code",
    [
        (test_job_finished, json.dumps({"sha": "test_commit"}), 200),
        (None, json.dumps({"sha": "test_commit"}), 404),
        (None, "invalid", 422),
    ],
)
@mock.patch("murdock.murdock.Murdock.start_commit_job")
def test_start_manual_commit_job(start_commit_job, result, data, code):
    start_commit_job.return_value = result
    response = client.post("/job/commit", data=data)
    assert response.status_code == code
    if result is not None:
        assert response.json() == result.dict(exclude_none=True)


@mock.patch("murdock.murdock.Murdock.get_job")
def test_get_job_not_found(get_job):
    get_job.return_value = None
    response = client.get("/job/12345")
    assert response.status_code == 404
    assert response.json() == {"detail": "No job matching uid '12345' found"}


@mock.patch("murdock.murdock.Murdock.remove_ws_client")
@mock.patch("murdock.murdock.Murdock.add_ws_client")
def test_ws_client(add_ws_client, remove_ws_client, caplog):
    caplog.set_level(logging.DEBUG, logger="murdock")
    with client.websocket_connect("/ws/status") as websocket:
        assert "websocket opening" in caplog.text
    assert "websocket connection opened" in caplog.text
    websocket.close()
    assert "websocket connection closed" in caplog.text
    add_ws_client.assert_called_once()
    remove_ws_client.assert_called_once()
