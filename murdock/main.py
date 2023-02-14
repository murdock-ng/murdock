import hmac
import hashlib
import json

from typing import List

import httpx

from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    Security,
    Depends,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.security.api_key import APIKeyHeader, APIKey
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from jinja2 import FileSystemLoader, Environment

from murdock import TEMPLATES_DIR
from murdock.config import (
    GLOBAL_CONFIG,
    DB_CONFIG,
    NOTIFIER_CONFIG,
    MAIL_NOTIFIER_CONFIG,
    MATRIX_NOTIFIER_CONFIG,
    GITHUB_CONFIG,
    CI_CONFIG,
    MurdockSettings,
)
from murdock.models import (
    JobModel,
    ManualJobBranchParamModel,
    ManualJobTagParamModel,
    ManualJobCommitParamModel,
    JobQueryModel,
)
from murdock.murdock import Murdock
from murdock.log import LOGGER
from murdock.github import check_permissions


LOGGER.debug(
    "Configuration:\n"
    f"\nGLOBAL_CONFIG:\n{json.dumps(GLOBAL_CONFIG.dict(), indent=4)}\n"
    f"\nDB_CONFIG:\n{json.dumps(DB_CONFIG.dict(), indent=4)}\n"
    f"\nNOTIFIER_CONFIG:\n{json.dumps(NOTIFIER_CONFIG.dict(), indent=4)}\n"
    f"\nMAIL_NOTIFIER_CONFIG:\n{json.dumps(MAIL_NOTIFIER_CONFIG.dict(), indent=4)}\n"
    f"\nMATRIX_NOTIFIER_CONFIG:\n{json.dumps(MATRIX_NOTIFIER_CONFIG.dict(), indent=4)}\n"
    f"\nGITHUB_CONFIG:\n{json.dumps(GITHUB_CONFIG.dict(), indent=4)}\n"
    f"\nCI_CONFIG:\n{json.dumps(CI_CONFIG.dict(), indent=4)}\n"
    f"\nMurdock default:\n{json.dumps(MurdockSettings().dict(), indent=4)}\n"
)


murdock = Murdock(repository=GITHUB_CONFIG.repo)
app = FastAPI(
    debug=GLOBAL_CONFIG.log_level == "DEBUG",
    on_shutdown=[murdock.shutdown],
    title="Murdock API",
    description="This is the Murdock API",
    version="1.0.0",
    docs_url="/api",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/results",
    StaticFiles(directory=GLOBAL_CONFIG.work_dir, html=True, check_dir=False),
    name="results",
)
murdock.instrumentator.instrument(app).expose(app)


@app.on_event("startup")
async def startup():
    await murdock.init()


@app.post("/github/webhook", include_in_schema=False)
async def github_webhook_handler(request: Request):
    headers = request.headers
    expected_signature = hmac.new(
        key=bytes(GITHUB_CONFIG.webhook_secret, "utf-8"),
        msg=(body := await request.body()),
        digestmod=hashlib.sha256,
    ).hexdigest()
    gh_signature = headers.get("X-Hub-Signature-256").split("sha256=")[-1].strip()
    if not hmac.compare_digest(gh_signature, expected_signature):
        LOGGER.warning(msg := "Invalid event signature")
        raise HTTPException(status_code=400, detail=msg)

    event_type = headers.get("X-Github-Event")
    if event_type not in GLOBAL_CONFIG.accepted_events:
        raise HTTPException(status_code=400, detail="Unsupported event")

    event_data = json.loads(body.decode())
    ret = None
    if event_type == "pull_request":
        ret = await murdock.handle_pull_request_event(event_data)
    if event_type == "push":
        ret = await murdock.handle_push_event(event_data)
    if ret is not None:
        raise HTTPException(status_code=400, detail=ret)


@app.get("/github/authenticate/{code}", include_in_schema=False)
async def github_authenticate_handler(code: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CONFIG.app_client_id,
                "client_secret": GITHUB_CONFIG.app_client_secret,
                "code": code,
            },
            headers={"Accept": "application/vnd.github.v3+json"},
        )
    return JSONResponse({"token": response.json()["access_token"]})


async def _check_push_permissions(
    token: str = Security(
        APIKeyHeader(
            name="authorization", scheme_name="Github OAuth Token", auto_error=False
        )
    )
) -> str:
    return await check_permissions("push", token)


async def _check_admin_permissions(
    token: str = Security(
        APIKeyHeader(
            name="authorization", scheme_name="Github OAuth Token", auto_error=False
        )
    )
):
    await check_permissions("admin", token)


@app.get(
    path="/jobs",
    response_model=List[JobModel],
    response_model_exclude_none=True,
    summary="Return the list of all jobs",
    tags=["jobs"],
)
async def jobs_handler(query: JobQueryModel = Depends()):
    return await murdock.get_jobs(query)


@app.delete(
    path="/jobs",
    response_model=List[JobModel],
    response_model_exclude_none=True,
    summary="Removed finished jobs older than 'before' date",
    tags=["jobs"],
)
async def finished_job_delete_handler(
    before: str, _: APIKey = Depends(_check_admin_permissions)
):
    query = JobQueryModel(before=before)
    return await murdock.remove_finished_jobs(query)


@app.post(
    path="/job/branch",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Start a manual job on a branch",
    tags=["branch job"],
)
async def job_start_branch_handler(
    param: ManualJobBranchParamModel, token: APIKey = Depends(_check_push_permissions)
):
    if (job := await murdock.start_branch_job(token, param)) is None:
        raise HTTPException(status_code=404, detail="No matching branch found")

    return job


@app.get(
    path="/job/branch/{branch}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Return the last job of the given branch",
    tags=["branch job"],
)
async def job_get_last_branch_handler(branch: str):
    query = JobQueryModel(branch=branch, limit=1)
    if not (jobs := await murdock.get_jobs(query)):
        raise HTTPException(
            status_code=404, detail=f"No matching job found for branch '{branch}'"
        )

    return jobs[0]


@app.get(
    path="/job/branch/{branch}/badge",
    response_class=Response,
    summary="Return the last job badge of the given branch",
    tags=["branch job"],
)
async def job_get_last_branch_badge_handler(branch: str):
    query = JobQueryModel(branch=branch, states="running errored passed", limit=1)
    if not (jobs := await murdock.get_jobs(query)):
        raise HTTPException(
            status_code=404, detail=f"No matching job found for branch '{branch}'"
        )

    loader = FileSystemLoader(searchpath=TEMPLATES_DIR)
    env = Environment(
        loader=loader,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=True,
    )
    env.globals.update(zip=zip)
    template = env.get_template("badge.svg.j2")
    headers = {"ETag": jobs[0].uid, "Cache-Control": "no-cache"}
    return Response(
        template.render(state=jobs[0].state),
        headers=headers,
        media_type="image/svg+xml",
    )


@app.post(
    path="/job/tag",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Start a manual job on a tag",
    tags=["tag job"],
)
async def job_start_tag_handler(
    param: ManualJobTagParamModel, token: APIKey = Depends(_check_push_permissions)
):
    if (job := await murdock.start_tag_job(token, param)) is None:
        raise HTTPException(status_code=404, detail="No matching tag found")

    return job


@app.get(
    path="/job/tag/{tag}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Return the last job of the given tag",
    tags=["tag job"],
)
async def job_get_last_tag_handler(tag: str):
    query = JobQueryModel(tag=tag, limit=1)
    if not (jobs := await murdock.get_jobs(query)):
        raise HTTPException(
            status_code=404, detail=f"No matching job found for tag '{tag}'"
        )

    return jobs[0]


@app.post(
    path="/job/commit",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Start a manual job on a tag",
    tags=["commit job"],
)
async def job_start_commit_handler(
    param: ManualJobCommitParamModel, token: APIKey = Depends(_check_push_permissions)
):
    if (job := await murdock.start_commit_job(token, param)) is None:
        raise HTTPException(status_code=404, detail="No matching commit found")

    return job


@app.get(
    path="/job/commit/{sha}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Return the last job of the given commit",
    tags=["commit job"],
)
async def job_get_last_commit_handler(sha: str):
    query = JobQueryModel(sha=sha, limit=1)
    if not (jobs := await murdock.get_jobs(query)):
        raise HTTPException(
            status_code=404, detail=f"No matching job found for commit '{sha}'"
        )

    return jobs[0]


@app.get(
    path="/job/pr/{prnum}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Return the last job of the given PR number",
    tags=["pr job"],
)
async def job_get_last_prnum_handler(prnum: int):
    query = JobQueryModel(prnum=prnum, limit=1)
    if not (jobs := await murdock.get_jobs(query)):
        raise HTTPException(
            status_code=404, detail=f"No matching job found for PR #{prnum}"
        )

    return jobs[0]


@app.get(
    path="/job/{uid}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Return the details of a job",
    tags=["job"],
)
async def job_handler(uid: str):
    if (job := await murdock.get_job(uid)) is None:
        raise HTTPException(
            status_code=404, detail=f"No job matching uid '{uid}' found"
        )
    return job


@app.post(
    path="/job/{uid}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Restart a finished job",
    tags=["job"],
)
async def job_restart_handler(
    uid: str, token: APIKey = Depends(_check_push_permissions)
):
    if (job := await murdock.restart_job(uid, str(token))) is None:
        raise HTTPException(status_code=404, detail=f"Cannot restart job '{uid}'")
    return job.model()


@app.put(
    path="/job/{uid}/status",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Update the status of a running job",
    tags=["job"],
)
async def running_job_status_handler(request: Request, uid: str):
    msg = ""
    if (job := murdock.running.search_by_uid(uid)) is None:
        msg = f"No job running with uid {uid}"
    elif "Authorization" not in request.headers:
        msg = "Job token is missing"
    elif (
        "Authorization" in request.headers
        and request.headers["Authorization"] != job.token
    ):
        msg = "Invalid Job token"

    if msg:
        LOGGER.warning(f"Invalid request to control_handler: {msg}")
        raise HTTPException(status_code=400, detail=msg)

    data = await request.json()
    if (job := await murdock.handle_job_status_data(uid, data)) is None:
        raise HTTPException(status_code=404, detail=f"No job with uid '{uid}' found")

    return job.model()


@app.delete(
    path="/job/{uid}",
    response_model=JobModel,
    response_model_exclude_none=True,
    summary="Remove a job",
    tags=["job"],
)
async def job_remove_handler(uid: str, _: APIKey = Depends(_check_push_permissions)):
    if (job := await murdock.remove_job(uid)) is None:
        raise HTTPException(status_code=404, detail=f"No job with uid '{uid}' found")

    return job


@app.websocket("/ws/status")
async def ws_client_handler(websocket: WebSocket):
    LOGGER.debug("websocket opening")
    await websocket.accept()
    LOGGER.debug("websocket connection opened")
    murdock.add_ws_client(websocket)

    try:
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        LOGGER.debug("websocket connection closed")
        murdock.remove_ws_client(websocket)
