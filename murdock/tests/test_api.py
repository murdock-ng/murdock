import os
import json
import logging

from unittest import mock

import pytest

from httpx import Response

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ..main import app, _check_push_permissions, _check_admin_permissions
from ..job import MurdockJob
from ..models import (
    CategorizedJobsModel, CommitModel,
    FinishedJobModel, JobModel, JobQueryModel, PullRequestInfo
)


client = TestClient(app)
commit = CommitModel(sha="test", message="test message", author="test")
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
    labels=["test"]
)
test_job_queued = JobModel(
    uid="1234", commit=commit, prinfo=prinfo, since=12345.6, fasttracked=True
)
test_job_building = JobModel(
    uid="1234", commit=commit, prinfo=prinfo,
    since=12345.6, status={"status": "test"}
)
test_job_finished = FinishedJobModel(
    uid="1234", commit=commit, prinfo=prinfo,
    since=12345.6, status={"status": "test"},
    result="passed", output_url="test", runtime=1234.5,
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
    "event,event_type,code,msg,handle_msg", [
        (
            {"event": "test_data_invalid"}, "pull_request",
            400, {"detail": "Invalid event signature"}, None
        ),
        (
            {"event": "test_data_invalid"}, "push",
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
            {"event": "test_data"}, "push",
            400, {"detail": "Murdock msg"}, "Murdock msg"
        ),
        (
            {"event": "test_data"}, "pull_request", 200, None, None
        ),
        (
            {"event": "test_data"}, "push", 200, None, None
        ),
    ]
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
            "Content-Type:": "application/vnd.github.v3+json"
        }
    )
    if event == {'event': 'test_data'} and event_type == "pull_request":
        handle_pr.assert_called_with(event)
    elif event == {'event': 'test_data'} and event_type == "push":
        handle_push.assert_called_with(event)
    else:
        handle_pr.assert_not_called()
        handle_push.assert_not_called()
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,code,valid", [
        (
            "error", 403, False
        ),
        (
            json.dumps({"permissions": {"admin": False}}), 200, False
        ),
        (
            json.dumps({"permissions": {"admin": True}}), 200, True
        ),
    ]
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
            "Authorization": "token token"
        }
    )


@pytest.mark.parametrize("result", [[], [test_job_queued.dict(exclude={"status"})]])
@mock.patch("murdock.murdock.Murdock.get_queued_jobs")
def test_get_queued_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/jobs/queued")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "job_queued,code", [
        pytest.param(None, 404, id="no_job_queued"),
        pytest.param(MurdockJob(commit, pr=prinfo), 200, id="one_job_queued"),
    ]
)
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job")
def test_cancel_queued_job(cancel, search, job_queued, code):
    search.return_value = job_queued
    response = client.delete(f"/jobs/queued/abcdef")
    assert response.status_code == code
    if job_queued:
        cancel.assert_called_with(job_queued, reload_jobs=True)
        assert response.json() == job_queued.queued_model().dict(exclude={"status", "output"})
    else:
        cancel.assert_not_called()
        assert response.json() == {"detail": "No job with uid 'abcdef' found"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.cancel_queued_job")
def test_cancel_queued_not_allowed(cancel):
    cancel.return_value = MurdockJob(commit, pr=prinfo)
    response = client.delete(f"/jobs/queued/abcdef")
    cancel.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize(
    "result",
    [
        [],
        [test_job_building.dict(exclude={"fasttracked"})]
    ]
)
@mock.patch("murdock.murdock.Murdock.get_active_jobs")
def test_get_building_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/jobs/building")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize(
    "job_running,job_found,headers,code,expected_response", [
        pytest.param(
            None, None, {},
            400, {"detail": "No job running with uid abcdef"},
            id="job_not_found"
        ),
        pytest.param(
            test_job, None, {},
            400, {"detail": "Job token is missing"},
            id="missing_job_token"
        ),
        pytest.param(
            test_job, None, {"Authorization": "invalid"},
            400, {"detail": "Invalid Job token"},
            id="invalid_job_token"
        ),
        pytest.param(
            test_job, None, {"Authorization": test_job.token},
            404, {"detail": "No job with uid 'abcdef' found"},
            id="update_job_not_found"
        ),
        pytest.param(
            test_job, test_job, {"Authorization": test_job.token},
            200, test_job.running_model().dict(exclude={"fasttracked", "output"}),
            id="update_job_found"
        ),
    ]
)
@mock.patch("murdock.murdock.Murdock.handle_job_status_data")
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
def test_update_running_job_status(
    running, commit_status,
    job_running, job_found, headers, code, expected_response
):
    running.return_value = job_running
    commit_status.return_value = job_found
    status = {"status": "test"}
    response = client.put(
        f"/jobs/building/abcdef/status",
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
    "result,code", [
        pytest.param(None, 404, id="job_not_found"),
        pytest.param(MurdockJob(commit, pr=prinfo), 200, id="job_found")
    ]
)
@mock.patch("murdock.murdock.Murdock.stop_active_job")
@mock.patch("murdock.job_containers.MurdockJobListBase.search_by_uid")
def test_stop_running_job(search, stop, result, code):
    search.return_value = result
    response = client.delete(f"/jobs/building/abcdef")
    assert response.status_code == code
    if result is not None:
        stop.assert_called_with(result, reload_jobs=True)
        assert response.json() == result.running_model().dict(exclude={"fasttracked", "output"})
    else:
        stop.assert_not_called()
        assert response.json() == {"detail": "No job with uid 'abcdef' found"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.stop_active_job")
def test_stop_running_not_allowed(stop):
    stop.return_value = MurdockJob(commit, pr=prinfo)
    response = client.delete(f"/jobs/building/abcdef")
    stop.assert_not_called()
    assert response.status_code == 401


@pytest.mark.parametrize("result", [[], [test_job_finished]])
@mock.patch("murdock.database.Database.find_jobs")
def test_get_finished_jobs(jobs, result):
    jobs.return_value = result
    response = client.get("/jobs/finished")
    assert response.status_code == 200
    assert response.json() == result
    jobs.assert_called_with(JobQueryModel())


@pytest.mark.parametrize("query,call_arg", [
    ("", JobQueryModel()),
    ("?limit=30", JobQueryModel(limit=30)),
    ("?limit=30&uid=12345", JobQueryModel(limit=30, uid="12345")),
    ("?prnum=42", JobQueryModel(prnum=42)),
    ("?branch=test", JobQueryModel(branch="test")),
    ("?sha=abcdef", JobQueryModel(sha="abcdef")),
    ("?author=me", JobQueryModel(author="me")),
    ("?result=passed", JobQueryModel(result="passed")),
    ("?after=after", JobQueryModel(after="after")),
    ("?before=before", JobQueryModel(before="before")),
    ("?invalid=invalid", JobQueryModel()),
])
@mock.patch("murdock.database.Database.find_jobs")
def test_get_finished_jobs_with_query(jobs, query, call_arg):
    jobs.return_value = [test_job_finished]
    client.get(f"/jobs/finished/{query}")
    jobs.assert_called_with(call_arg)


@pytest.mark.usefixtures("push_allowed")
@pytest.mark.parametrize(
    "result,code", [(None, 404), (MurdockJob(commit, pr=prinfo), 200)]
)
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job(restart, result, code):
    restart.return_value = result
    response = client.post("/jobs/finished/123")
    assert response.status_code == code
    restart.assert_called_with("123")
    if result is not None:
        assert response.json() == result.queued_model().dict(exclude={"status", "output"})
    else:
        assert response.json() == {"detail": f"Cannot restart job '123'"}


@pytest.mark.usefixtures("push_not_allowed")
@mock.patch("murdock.murdock.Murdock.restart_job")
def test_restart_job_not_allowed(restart):
    restart.return_value = MurdockJob(commit, pr=prinfo)
    response = client.post("/jobs/finished/123")
    restart.assert_not_called()
    assert response.status_code == 401


@pytest.mark.usefixtures("admin_allowed")
@pytest.mark.parametrize(
    "result,code", [([], 404), ([test_job_finished], 200)]
)
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_job(remove, result, code):
    remove.return_value = result
    before = "2021-08-16"
    response = client.delete(f"/jobs/finished?before={before}")
    assert response.status_code == code
    remove.assert_called_with(JobQueryModel(before=before))
    if result:
        assert response.json() == [job.dict(exclude_none=True) for job in result]
    else:
        assert response.json() == {"detail": "Found no finished job to remove"}


@pytest.mark.usefixtures("admin_not_allowed")
@mock.patch("murdock.murdock.Murdock.remove_finished_jobs")
def test_delete_job_not_allowed(remove):
    remove.return_value = [test_job_finished]
    before = "2021-08-16"
    response = client.delete(f"/jobs/finished?before={before}")
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
    response = client.get("/jobs")
    assert response.status_code == 200
    assert CategorizedJobsModel(**response.json()) == result


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
