import os
import json

import httpx

from jinja2 import FileSystemLoader, Environment

from murdock.config import GLOBAL_CONFIG, GITHUB_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


async def comment_on_pr(job: MurdockJob):
    loader = FileSystemLoader(searchpath=TEMPLATES_DIR)
    env = Environment(
        loader=loader, trim_blocks=True, lstrip_blocks=True,
        keep_trailing_newline=True
    )
    env.globals.update(zip=zip)
    template = env.get_template("comment.md.j2")
    context = {
        "job": job,
        "sticky_comment": GLOBAL_CONFIG.sticky_comment
    }
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
    if GLOBAL_CONFIG.sticky_comment is True:
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
                data=request_data
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
                data=request_data
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
                "Accept": "application/vnd.github.v3+json"
            }
        )
        if response.status_code != 200:
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
            }
            , data=json.dumps(status)
        )
        if response.status_code != 201:
            LOGGER.warning(f"{response}: {response.json()}")
