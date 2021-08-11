import hmac
import hashlib
import json

from typing import Optional

import httpx

from fastapi import (
    FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
)
from fastapi.responses import JSONResponse

from murdock.config import (
    MURDOCK_LOG_LEVEL, MURDOCK_USE_JOB_TOKEN,
    MURDOCK_GITHUB_APP_CLIENT_ID, MURDOCK_GITHUB_APP_CLIENT_SECRET,
    MURDOCK_MAX_FINISHED_LENGTH_DEFAULT, GITHUB_WEBHOOK_SECRET, GITHUB_REPO
)
from murdock.murdock import Murdock
from murdock.log import LOGGER


murdock = Murdock()
app = FastAPI(
    debug=MURDOCK_LOG_LEVEL == "DEBUG",
    on_startup=[murdock.init],
    on_shutdown=[murdock.shutdown],
    title="Murdock API",
    description="This is the Murdock API",
    version="1.0.0",
    docs_url="/api", redoc_url=None,
)


@app.post("/github/webhook", include_in_schema=False)
async def github_webhook_handler(request: Request):
    headers = request.headers
    body = await request.body()
    secret = bytes(GITHUB_WEBHOOK_SECRET, "utf-8")
    expected_signature = hmac.new(
        key=secret,
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()
    gh_signature = headers["X-Hub-Signature-256"].split('sha256=')[-1].strip()
    if not hmac.compare_digest(gh_signature, expected_signature):
        msg = "Invalid webhook token"
        LOGGER.warning(msg)
        return HTTPException(status_code=400, detail=msg)

    if request.headers.get("X-Github-Event") == "pull_request":
        LOGGER.info("Handle pull request event")
        event_data = json.loads(body.decode())
        ret = await murdock.handle_pull_request_event(event_data)
        if ret is not None:
            raise HTTPException(status_code=400, detail=ret)


@app.get("/github/authenticate/{code}", include_in_schema=False)
async def github_authenticate_handler(code: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": MURDOCK_GITHUB_APP_CLIENT_ID,
                "client_secret": MURDOCK_GITHUB_APP_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/vnd.github.v3+json"}
        )
    return _json_response({"token": response.json()["access_token"]})


def _json_response(data):
    response = JSONResponse(data)
    response.headers.update(
        {
            "Access-Control-Allow-Credentials" : "false",
            "Access-Control-Allow-Origin" : "*",
        }
    )
    return response


async def _check_push_permissions(token: str) -> bool:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_REPO}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}"
            }
        )
    if response.status_code != 200:
        LOGGER.warning(f"Cannot fetch push permissions ({response})")

    return (
        response.status_code == 200 and response.json()["permissions"]["push"]
    )


@app.get(
    path="/api/jobs/queued",
    summary="Return the list of queued jobs"
)
async def queued_jobs_handler():
    return _json_response(murdock.get_queued_jobs())


@app.options("/api/jobs/queued/{commit}", include_in_schema=False)
async def queued_commit_cancel_handler():
    response = JSONResponse({})
    response.headers.update(
        {
            "Access-Control-Allow-Credentials" : "false",
            "Access-Control-Allow-Origin" : "*",
            "Access-Control-Allow-Methods" : "OPTIONS,DELETE",
            "Access-Control-Allow-Headers" : "authorization,content-type",
        }
    )
    return response


@app.delete(
    path="/api/jobs/queued/{commit}",
    summary="Remove a job from the queue"
)
async def queued_commit_cancel_handler(request: Request, commit: str):
    msg = ""
    if "Authorization" not in request.headers:
        msg = "Authorization token is missing"
        LOGGER.warning(f"Invalid request: {msg}")
        raise HTTPException(status_code=400, detail=msg)

    can_push = await _check_push_permissions(request.headers["Authorization"])
    if not can_push:
        raise HTTPException(status_code=403, detail="Missing push permissions")

    await murdock.cancel_queued_job_with_commit(commit)

    return _json_response({})


@app.get(
    path="/api/jobs/building",
    summary="Return the list of building jobs"
)
async def building_jobs_handler():
    return _json_response(murdock.get_running_jobs())


@app.put(
    path="/api/jobs/building/{commit}/status",
    summary="Update the status of a building job"
)
async def building_commit_status_handler(request: Request, commit: str):
    data = await request.json()

    msg = ""
    if MURDOCK_USE_JOB_TOKEN:
        job = murdock.job_running(commit)
        if job is None:
            msg = f"No job running for commit {commit}"
        if "Authorization" not in request.headers:
            msg = "Job token is missing"
        if request.headers["Authorization"] != job.token:
            msg = "Invalid API token"

    if msg:
        LOGGER.warning(f"Invalid request to control_handler: {msg}")
        raise HTTPException(status_code=400, detail=msg)

    await murdock.handle_commit_status_data(commit, data)


@app.options("/api/jobs/building/{commit}", include_in_schema=False)
async def building_commit_stop_handler():
    response = JSONResponse({})
    response.headers.update(
        {
            "Access-Control-Allow-Credentials" : "false",
            "Access-Control-Allow-Origin" : "*",
            "Access-Control-Allow-Methods" : "OPTIONS,DELETE",
            "Access-Control-Allow-Headers" : "authorization,content-type",
        }
    )
    return response


@app.delete(
    path="/api/jobs/building/{commit}",
    summary="Stop a building job"
)
async def building_commit_stop_handler(request: Request, commit: str):
    msg = ""
    if "Authorization" not in request.headers:
        msg = "Authorization token is missing"
        LOGGER.warning(f"Invalid request: {msg}")
        raise HTTPException(status_code=400, detail=msg)

    can_push = await _check_push_permissions(request.headers["Authorization"])

    if not can_push:
        raise HTTPException(status_code=403, detail="Missing push permissions")

    await murdock.stop_running_job(commit)

    return _json_response({})


@app.get(
    path="/api/jobs/finished",
    summary="Return the list of finished jobs sorted by end time, reversed"
)
async def finished_jobs_handler(
        limit: Optional[int] = MURDOCK_MAX_FINISHED_LENGTH_DEFAULT,
        job_id: Optional[str] = None,
        prnum: Optional[int] = None,
        user: Optional[str] = None,
        result: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
):
    data = await murdock.get_finished_jobs(
        limit, job_id, prnum, user, result, from_date, to_date
    )
    return _json_response(data)


@app.get(
    path="/api/jobs",
    summary="Return the list of all jobs (queued, building, finished)")
async def jobs_handler(
    limit: Optional[int] = MURDOCK_MAX_FINISHED_LENGTH_DEFAULT
):
    data = await murdock.get_jobs(limit)
    return _json_response(data)


@app.websocket("/ws/status")
async def ws_client_handler(websocket: WebSocket):
    LOGGER.debug('websocket opening')
    await websocket.accept()
    LOGGER.debug('websocket connection opened')
    murdock.add_ws_client(websocket)

    try:
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        LOGGER.debug('websocket connection closed')
        murdock.remove_ws_client(websocket)
