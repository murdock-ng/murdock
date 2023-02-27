import base64
import json
from typing import Optional

import httpx
import yaml
import pydantic

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from jinja2 import FileSystemLoader, Environment

from murdock import TEMPLATES_DIR
from murdock.config import GITHUB_CONFIG, GLOBAL_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel
from murdock.config import MurdockSettings


MAX_PAGES_COUNT = 10


async def check_permissions(
    level: str,
    token: str = Security(
        APIKeyHeader(
            name="authorization", scheme_name="Github OAuth Token", auto_error=False
        )
    ),
) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}",
            },
        )

    if response.status_code != 200:
        LOGGER.warning("Cannot fetch push permissions", response=str(response))

    if response.status_code == 200 and response.json()["permissions"][level]:
        return token

    raise HTTPException(status_code=401, detail=f"Missing {level} permissions")


async def comment_on_pr(job: MurdockJob):
    logger = LOGGER.bind(job=job.uid)
    if job.pr:
        logger = logger.bind(pr=job.pr.number)
    if GLOBAL_CONFIG.enable_pr_comment is False:
        logger.debug("Skipping pr comment, disabled by config")
        return

    if (
        job.pr is None
        or job.config is None
        or job.config.pr is None
        or job.config.pr.enable_comments is False
    ):
        return
    loader = FileSystemLoader(searchpath=TEMPLATES_DIR)
    env = Environment(
        loader=loader,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=True,
    )
    env.globals.update(zip=zip)
    template = env.get_template("comment.md.j2")
    context = {"job": job, "base_url": GLOBAL_CONFIG.base_url}
    issues_comments_url = (
        f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
        f"/issues/{job.pr.number}/comments"
    )
    request_headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {GITHUB_CONFIG.api_token}",
    }
    request_data = json.dumps({"body": template.render(**context)})

    comment_id = None
    if job.config.pr.sticky_comment is True:
        async with httpx.AsyncClient() as client:
            page = 1
            while (
                comment_id is None
                and (
                    response := await client.get(
                        f"{issues_comments_url}?page={page}",
                        headers=request_headers,
                    )
                ).json()
                and page < MAX_PAGES_COUNT
            ):
                for comment in response.json():
                    if comment["body"].split("\n")[0] == "### Murdock results":
                        comment_id = comment["id"]
                        break
                page += 1

    if comment_id is None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                issues_comments_url, headers=request_headers, content=request_data
            )
        if response.status_code != 201:
            logger.error(
                "Unable to post comment on PR",
                response=str(response),
                status_code=response.status_code,
                content=str(response.text),
            )
        else:
            logger.info("Comment posted on PR")
    else:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                (
                    f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
                    f"/issues/comments/{comment_id}"
                ),
                headers=request_headers,
                content=request_data,
            )
        logger = logger.bind(response=str(response))
        if response.status_code != 200:
            logger.error("Failed to put comment on PR", content=str(response.text))
        else:
            logger.info("Comment posted on PR")


async def fetch_commit_info(commit: str) -> Optional[CommitModel]:
    logger = LOGGER.bind(commit=commit)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/commits/{commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}",
            },
        )
        if response.status_code != 200:
            logger.error(
                "Failed to fetch commit",
                response=str(response),
                content=str(response.text),
            )
            return None

        commit_data = response.json()
        author = (
            commit_data["commit"]["author"]["name"]
            if commit_data["author"] is None
            else commit_data["author"]["login"]
        )

        return CommitModel(
            sha=commit_data["sha"],
            tree=commit_data["commit"]["tree"]["sha"],
            message=commit_data["commit"]["message"],
            author=author,
        )


async def fetch_branch_info(branch: str) -> Optional[CommitModel]:
    logger = LOGGER.bind(branch=branch)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/branches/{branch}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}",
            },
        )
        if response.status_code != 200:
            logger.error(
                "Failed to fetch branch",
                response=str(response),
                content=str(response.text),
            )
            return None

        branch_data = response.json()
        return await fetch_commit_info(branch_data["commit"]["sha"])


async def fetch_tag_info(tag: str) -> Optional[CommitModel]:
    logger = LOGGER.bind(tag=tag)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/git/refs/tags/{tag}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}",
            },
        )
        if response.status_code != 200:
            logger.error(
                "Failed to fetch tag",
                response=str(response),
                content=str(response.text),
            )
            return None

        tag_data = response.json()
        return await fetch_commit_info(tag_data["object"]["sha"])


async def fetch_user_login(token: str) -> Optional[str]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}",
            },
        )
        if response.status_code != 200:
            LOGGER.debug(f"Failed to fetch user info: {response} {response.text}")
            return None

        user_data = response.json()
        return user_data["login"]


async def set_commit_status(commit: str, status: dict):
    logger = LOGGER.bind(commit=str)
    if GLOBAL_CONFIG.enable_commit_status is False:
        LOGGER.debug("Skipping commit status update, disabled by config")
        return None

    LOGGER.debug("Setting commit status description", description=status["description"])
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}/statuses/{commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}",
            },
            content=json.dumps(status),
        )
        if response.status_code != 201:
            logger.error(
                "Unable to set commit status",
                response=str(response),
                status_code=response.status_code,
                content=str(response.text),
            )


async def fetch_murdock_config(commit: str) -> MurdockSettings:
    logger = LOGGER.bind(commit=str)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{GITHUB_CONFIG.repo}"
            f"/contents/.murdock.yml?ref={commit}",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_CONFIG.api_token}",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "No config file found, using default config",
                response=str(response),
                status_code=response.status_code,
            )
            return MurdockSettings()

        try:
            content = yaml.safe_load(
                base64.b64decode(response.json()["content"]).decode(),
            )
        except yaml.YAMLError as exc:
            logger.error("Cannot parse config file", exception=str(exc))
            return MurdockSettings()

        if not content:
            return MurdockSettings()

        try:
            return MurdockSettings(**content)
        except pydantic.error_wrappers.ValidationError:
            return MurdockSettings()
