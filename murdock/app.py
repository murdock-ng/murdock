import aiohttp
from aiohttp import web
from gidgethub import sansio

from murdock.config import (
    MURDOCK_HTTP_PORT, MURDOCK_USE_SECURE_API, MURDOCK_API_SECRET,
    MURDOCK_MAX_FINISHED_LENGTH_DEFAULT, GITHUB_WEBHOOK_SECRET
)
from murdock.murdock import Murdock
from murdock.log import LOGGER


async def github_webhook_handler(request):
    LOGGER.info(f"{request.method} {request.url}")
    murdock = request.app["murdock"]
    headers = request.headers
    body = await request.read()
    try:
        event = sansio.Event.from_http(
            headers, body, secret=GITHUB_WEBHOOK_SECRET
        )
    except sansio.ValidationFailure as exc:
        LOGGER.debug(exc)
        return web.HTTPBadRequest(reason=repr(exc))

    ret = web.HTTPAccepted()
    if "pull_request" in event.data:
        LOGGER.info("Handle pull request event")
        ret = await murdock.handle_pull_request_event(event)

    return ret


async def control_handler(request):
    LOGGER.info(f"{request.method} {request.url}")
    murdock = request.app["murdock"]
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
        return web.HTTPBadRequest(reason=msg)

    await murdock.handle_ctrl_data(data)
    return web.json_response(data)


async def pull_requests_handler(request):
    LOGGER.info(f"{request.method} {request.url}")
    if "max_length" in request.rel_url.query:
        max_length = int(request.rel_url.query["max_length"])
    else:
        max_length = MURDOCK_MAX_FINISHED_LENGTH_DEFAULT
    response = await request.app["murdock"].pulls(max_length)
    return web.json_response(
        response,
        headers={
            "Access-Control-Allow-Credentials": "false",
            "Access-Control-Allow-Origin": "*",
        }
    )


async def ws_client_handler(request):
    LOGGER.info(f"{request.method} {request.url}")
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    LOGGER.debug('websocket connection opened')
    murdock = request.app["murdock"]
    murdock.add_ws_client(ws)

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            LOGGER.debug(msg.data)
            await ws.send_str(msg.data + '/answer')
        elif msg.type == aiohttp.WSMsgType.ERROR:
            LOGGER.debug(
                f"ws connection closed with exception {ws.exception()}"
            )
    LOGGER.debug('websocket connection closed')

    murdock.remove_ws_client(ws)
    return ws


async def create_app():
    app = web.Application()
    murdock = Murdock()
    await murdock.init()
    app["murdock"] = murdock
    app.router.add_get('/api/pull_requests', pull_requests_handler, name='prs')
    app.router.add_get('/status', ws_client_handler, name='status')
    app.router.add_post('/github', github_webhook_handler, name='webhook')
    app.router.add_post('/ctrl', control_handler, name='ctrl')
    return app


def main():
    LOGGER.info(
        f"Murdock started and listening on port {MURDOCK_HTTP_PORT}"
    )
    web.run_app(create_app(), host='0.0.0.0', port=MURDOCK_HTTP_PORT)
