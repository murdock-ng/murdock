import hmac
import hashlib
import json
import logging

from typing import Optional

from fastapi import (
    FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
)
from fastapi.responses import JSONResponse

from murdock.config import (
    MURDOCK_LOG_LEVEL, MURDOCK_USE_SECURE_API, MURDOCK_API_SECRET,
    MURDOCK_MAX_FINISHED_LENGTH_DEFAULT, GITHUB_WEBHOOK_SECRET
)
from murdock.murdock import Murdock
from murdock.log import LOGGER


murdock = Murdock()
app = FastAPI(
    debug=MURDOCK_LOG_LEVEL == logging.DEBUG,
    on_startup=[murdock.init],
    title="Murdock API",
    description="This is the Murdock API",
    version="1.0.0",
)


@app.post("/github", include_in_schema=False)
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


@app.post("/api/commit/status", include_in_schema=False)
async def commit_status_handler(request: Request):
    data = await request.json()

    msg = ""
    if MURDOCK_USE_SECURE_API:
        if "secret" not in data:
            msg = "API token is missing"
        if data["secret"] != MURDOCK_API_SECRET:
            msg = "Invalid API token"
    if "commit" not in data:
        msg = "Missing commit hash"

    if msg:
        LOGGER.warning(f"Invalid request to control_handler: {msg}")
        raise HTTPException(status_code=400, detail=msg)

    await murdock.handle_commit_status_data(data)


@app.get("/api/jobs")
async def jobs_handler(
    max_length: Optional[int] = MURDOCK_MAX_FINISHED_LENGTH_DEFAULT
):
    jobs = await murdock.jobs(max_length)
    response = JSONResponse(jobs)
    response.headers.update(
        {
            "Access-Control-Allow-Credentials" : "false",
            "Access-Control-Allow-Origin" : "*",
        }
    )
    return response


@app.get("/api/pull/{prnum}")
async def pull_jobs_handler(prnum):
    jobs = await murdock.pull_jobs(prnum)
    response = JSONResponse(jobs)
    response.headers.update(
        {
            "Access-Control-Allow-Credentials" : "false",
            "Access-Control-Allow-Origin" : "*",
        }
    )
    return response


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
