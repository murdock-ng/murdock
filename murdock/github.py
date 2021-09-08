import base64
import os
import json

import httpx
import yaml

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from jinja2 import FileSystemLoader, Environment

from murdock.config import GLOBAL_CONFIG, GITHUB_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel
from murdock.config import MurdockSettings


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


async def check_permissions(
    level: str,
    token: str = Security(APIKeyHeader(
        name="authorization",
        scheme_name="Github OAuth Token",
        auto_error=False)
    ),
) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}"
            }
        )

    if response.status_code != 200:
        LOGGER.warning(f"Cannot fetch push permissions ({response})")

    if response.status_code == 200 and response.json()["permissions"][level]:
        return token

    raise HTTPException(status_code=401, detail=f"Missing {level} permissions")


async def comment_on_pr(job: MurdockJob):
    if job.pr is None or job.config.pr.enable_comments is False:
        return
    loader = FileSystemLoader(searchpath=TEMPLATES_DIR)
    env = Environment(
        loader=loader, trim_blocks=True, lstrip_blocks=True,
        keep_trailing_newline=True
    )
    env.globals.update(zip=zip)
    template = env.get_template("comment.md.j2")
    context = {"job": job}
    issues_comments_url = (
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
        f"/issues/{job.pr.number}/comments"
    )
    request_headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {GITHUB_CONFIG.api_token}"
    }
    request_data = json.dumps({"body": template.render(**context)})

    comment_id = None
    if job.config.pr.sticky_comment is True:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                issues_comments_url,
                headers=request_headers,
            )
        for comment in response.json():
            if comment["body"].split("\n")[0] == "### Murdock results":
                comment_id = comment["id"]
                break

    if comment_id is None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                issues_comments_url,
                headers=request_headers,
                content=request_data
            )
        if response.status_code != 201:
            LOGGER.warning(f"{response}: {response.json()}")
        else:
            LOGGER.info(f"Comment posted on PR #{job.pr.number}")
    else:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                (
                    f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
                    f"/issues/comments/{comment_id}"
                ),
                headers=request_headers,
                content=request_data
            )
        if response.status_code != 200:
            LOGGER.warning(f"{response}: {response.json()}")
        else:
            LOGGER.info(f"Comment posted on PR #{job.pr.number}")


async def fetch_commit_info(commit: str) -> CommitModel:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
            f"/commits/{commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            }
        )
        if response.status_code != 200:
            LOGGER.debug(
                f"Failed to fetch commit: {response} {response.json()}"
            )
            return

        commit_data = response.json()
        return CommitModel(
            sha=commit,
            message=commit_data["commit"]["message"],
            author=commit_data["author"]["login"],
        )


async def set_commit_status(commit: str, status: dict):
    LOGGER.debug(
        f"Setting commit {commit[0:7]} status to '{status['description']}'"
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/statuses/{commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            },
            content=json.dumps(status)
        )
        if response.status_code != 201:
            LOGGER.warning(f"{response}: {response.json()}")


async def fetch_murdock_config(commit: str) -> MurdockSettings:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
            f"/contents/.murdock.yml?ref={commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}"
            }
        )
        if response.status_code != 200:
            LOGGER.debug("No config file found, using default config")
            return MurdockSettings()

        try:
            content = yaml.load(
                base64.b64decode(response.json()["content"]).decode(),
                Loader=yaml.FullLoader
            )
        except yaml.YAMLError as exc:
            LOGGER.warning(f"Cannot parse config file: {exc}")
            return MurdockSettings()

        if not content:
            return MurdockSettings()

        return MurdockSettings(**content)
