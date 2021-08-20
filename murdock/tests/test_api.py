import os
import json
import logging

from unittest import mock

import pytest

from httpx import Response

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ..main import app, _check_push_permissions
from ..job import MurdockJob
from ..models import (
    CategorizedJobsModel, FinishedJobModel, JobModel, PullRequestInfo
)


client = TestClient(app)
prinfo = PullRequestInfo(
    title="test",
    number=123,
    merge_commit="test",
    branch="test",
    commit="test",
    commit_message="test message",
    user="test",
    url="test",
    base_repo="test",
    base_branch="test",
    base_commit="test",
    base_full_name="test",
    mergeable=True,
    labels=["test"]
)
test_job_queued = JobModel(
    uid="1234", prinfo=prinfo, since=12345.6, fasttracked=True
).dict()
test_job_building = JobModel(
    uid="1234", prinfo=prinfo, since=12345.6, status={"status": "test"}
).dict()
test_job_finished = FinishedJobModel(
    uid="1234", prinfo=prinfo, since=12345.6, status={"status": "test"},
    result="passed", output_url="test", runtime=1234.5, work_dir="/tmp"
).dict()
test_job = MurdockJob(prinfo)


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


def test_openapi_exists():
    response = client.get("/api")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "event,event_type,code,msg,handle_msg", [
        (
            {"event": "test_data_invalid"}, "pull_request",
            400, {"detail": "Invalid event signature"}, None
        ),
        (
            {"event": "test_data"}, "unsupported_event",
            400, {"detail": "Unsupported event"}, None
        ),
        (
            {"event": "test_data"}, "pull_request",
            400, {"detail": "Murdock msg"}, "Murdock msg"
        ),
        (
            {"event": "test_data"}, "pull_request", 200, None, None
        ),
    ]
)
@mock.patch("murdock.murdock.Murdock.handle_pull_request_event")
def test_github_webhook(
    handle, event, event_type, code, msg, handle_msg
):
    handle.return_value = handle_msg
    response = client.post(
        "/github/webhook",
        data=json.dumps(event),
        headers={
            "X-Hub-Signature-256": "sha256=c9f5bb344fb71f91afbac48f5afcd8421a3d0fbdddb3857a521e155cea75a43e",
            "X-Github-Event": event_type,
            "Content-Type:": "application/vnd.github.v3+json"
        }
    )
    if event == {'event': 'test_data'} and event_type == "pull_request":
        handle.assert_called_with(event)
    else:
        handle.assert_not_called()
    assert response.status_code == code
    if response.status_code != 200:
        assert response.json() == msg


@mock.patch("httpx.AsyncClient.post")
def test_github_authenticate(post):
    post.return_value =  Response(
        200, text=json.dumps({"access_token": "test_token"})
    )
    response = client.get("/github/authenticate/12345")
    post.assert_called_with(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": os.getenv("MURDOCK_GITHUB_APP_CLIENT_ID"),
            "client_secret": os.getenv("MURDOCK_GITHUB_APP_CLIENT_SECRET"),
            "code": "12345",
        },
        headers={"Accept": "application/vnd.github.v3+json"}
    )
    assert response.json() == {"token": "test_token"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,valid", [
        (
            "error", 403, False
        ),
        (
            json.dumps({"permissions": {"push": False}}), 200, False
        ),
        (
            json.dumps({"permissions": {"push": True}}), 200, True
        ),
    ]
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
        f"https://api.github.com/repos/{os.getenv('GITHUB_REPO')}",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": "token token"
        }
    )


@pytest.mark.parametrize("result", [[], [test_job_queued]])
@mock.patch("murdock.murdock.Murdock.get_queued_jobs")
def test_get_queued_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/api/jobs/queued")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [(None, 404), (MurdockJob(prinfo), 200)]
)
@mock.patch("murdock.murdock.Murdock.cancel_queued_job_with_commit")
def test_cancel_queued_job(cancel, result, code):
    cancel.return_value = result
    response = client.delete(f"/api/jobs/queued/abcdef")
    assert response.status_code == code
    cancel.assert_called_with("abcdef")
    if result:
        assert response.json() == result.queued_model()
    else:
        assert response.json() == {
            "detail": "No job matching commit 'abcdef' found"
        }


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job_with_commit")
def test_cancel_queued_not_allowed(cancel):
    cancel.return_value = MurdockJob(prinfo)
    response = client.delete(f"/api/jobs/queued/abcdef")
    cancel.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize("result", [[], [test_job_finished]])
@mock.patch("murdock.murdock.Murdock.get_running_jobs")
def test_get_building_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/api/jobs/building")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize(
    "job_running,job_found,headers,code,expected_response", [
        (
            None, None, {},
            400, {"detail": "No job running for commit abcdef"}
        ),
        (
            test_job, None, {},
            400, {"detail": "Job token is missing"}
        ),
        (
            test_job, None, {"Authorization": "invalid"},
            400, {"detail": "Invalid Job token"}
        ),
        (
            test_job, None, {"Authorization": test_job.token},
            404, {"detail": "No job matching commit 'abcdef' found"}
        ),
        (
            test_job, test_job, {"Authorization": test_job.token},
            200, test_job.running_model()
        ),
    ]
)
@mock.patch("murdock.murdock.Murdock.handle_commit_status_data")
@mock.patch("murdock.murdock.Murdock.job_running")
def test_update_running_job_status(
    running, commit_status,
    job_running, job_found, headers, code, expected_response
):
    running.return_value = job_running
    commit_status.return_value = job_found
    status = {"status": "test"}
    response = client.put(
        f"/api/jobs/building/abcdef/status",
        json.dumps(status),
        headers=headers
    )
    running.assert_called_with("abcdef")
    assert response.status_code == code
    if response.status_code in [200, 404]:
        commit_status.assert_called_with("abcdef", status)
    assert response.json() == expected_response


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [(None, 404), (MurdockJob(prinfo), 200)]
)
@mock.patch("murdock.murdock.Murdock.stop_running_job")
def test_stop_running_job(stop, result, code):
    stop.return_value = result
    response = client.delete(f"/api/jobs/building/abcdef")
    assert response.status_code == code
    stop.assert_called_with("abcdef")
    if result:
        assert response.json() == result.running_model()
    else:
        assert response.json() == {
            "detail": "No job matching commit 'abcdef' found"
        }


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.stop_running_job")
def test_stop_running_not_allowed(stop):
    stop.return_value = MurdockJob(prinfo)
    response = client.delete(f"/api/jobs/building/abcdef")
    stop.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize("result", [[], [test_job_finished]])
@mock.patch("murdock.murdock.Murdock.get_finished_jobs")
def test_get_finished_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/api/jobs/finished")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [(None, 404), (MurdockJob(prinfo), 200)]
)
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job(restart, result, code):
    restart.return_value = result
    response = client.post("/api/jobs/finished/123")
    assert response.status_code == code
    restart.assert_called_with("123")
    if result is not None:
        assert response.json() == result.queued_model()
    else:
        assert response.json() == {"detail": f"Cannot restart job '123'"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job_not_allowed(restart):
    restart.return_value = MurdockJob(prinfo)
    response = client.post("/api/jobs/finished/123")
    restart.assert_not_called()
    assert response.status_code == 401


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [([], 404), ([test_job_finished], 200)]
)
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_job(remove, result, code):
    remove.return_value = result
    before = "2021-08-16"
    response = client.delete(f"/api/jobs/finished?before={before}")
    assert response.status_code == code
    remove.assert_called_with(before)
    if result:
        assert response.json() == result
    else:
        assert response.json() == {"detail": "Found no finished job to remove"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_job_not_allowed(remove):
    remove.return_value = [test_job_finished]
    before = "2021-08-16"
    response = client.delete(f"/api/jobs/finished?before={before}")
    remove.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize(
    "result",
    [
        CategorizedJobsModel(queued=[], building=[], finished=[]),
        CategorizedJobsModel(
            queued=[test_job_queued], building=[], finished=[]
        ),
        CategorizedJobsModel(
            queued=[test_job_queued],
            building=[test_job_building],
            finished=[]
        ),
        CategorizedJobsModel(
            queued=[test_job_queued],
            building=[test_job_building],
            finished=[test_job_finished]
        ),
    ]
)
@mock.patch("murdock.murdock.Murdock.get_jobs")
def test_get_jobs(jobs, result):
    jobs.return_value = result.dict()
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert CategorizedJobsModel(**response.json()) == result


# @pytest.mark.usefixtures("log_level_debug")
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
