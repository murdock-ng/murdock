import json

from abc import ABC, abstractmethod
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

import httpx
import aiosmtplib

from murdock.config import GITHUB_CONFIG
from murdock.database import Database
from murdock.job import MurdockJob
from murdock.log import LOGGER
from murdock.models import JobQueryModel
from murdock.config import MAIL_NOTIFIER_CONFIG, MATRIX_NOTIFIER_CONFIG, NOTIFIER_CONFIG


class NotifierBase(ABC):
    @abstractmethod
    async def notify(self, job: MurdockJob):
        ...  # pragma: no cover


class MailNotifier(NotifierBase):
    config = MAIL_NOTIFIER_CONFIG

    async def notify(self, job: MurdockJob):
        title = f"Murdock job {job.state} - {job.title}"
        content = f"Details: {job.details_url}"

        message = EmailMessage()
        message["From"] = "ci@riot-os.org"
        message["To"] = ";".join(self.config.recipients)
        message["Subject"] = title
        message["Date"] = datetime.now()
        message.set_content(content)

        try:
            await aiosmtplib.send(
                message,
                hostname=self.config.server,
                port=self.config.port,
                start_tls=self.config.use_tls,
                username=self.config.username,
                password=self.config.password,
            )
        except (
            aiosmtplib.errors.SMTPAuthenticationError,
            aiosmtplib.errors.SMTPConnectError,
            aiosmtplib.errors.SMTPServerDisconnected,
            aiosmtplib.errors.SMTPTimeoutError,
        ) as exc:
            LOGGER.debug(f"Cannot send email: {exc}")
        else:
            LOGGER.debug(f"Notification email sent to '{self.config.recipients}'")


class MatrixNotifier(NotifierBase):
    config = MATRIX_NOTIFIER_CONFIG

    async def _get_member_id(self, author: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://matrix.org/_matrix/client/v3/rooms/%21{self.config.room[1:]}"
                    f"/joined_members?access_token={self.config.token}",
                )
                if response.status_code != 200:
                    LOGGER.debug(
                        f"Cannot fetch members of room '{self.config.room}': {response} {response.json()}"
                    )
                    return None
        except httpx.ReadTimeout as exc:
            LOGGER.warning(
                f"Failed to get Matrix member id for author '{author}': {exc}"
            )
            return None
        for member_id, info in response.json()["joined"].items():
            if info["display_name"] == author:
                return member_id
        return None

    async def notify(self, job: MurdockJob):
        job_state_mapping = {"passed": "PASSED", "errored": "FAILED"}
        job_state = job_state_mapping[job.state] if job.state is not None else "unknown"
        emoji = "&#x2705;" if job_state == "PASSED" else "&#x274C;"
        content = f"Job {job.state} - {job.title}: {job.details_url}"
        commit_short = job.commit.sha[0:7]
        commit_url = f"https://github.com/{GITHUB_CONFIG.repo}/commit/{job.commit.sha}"
        if job.pr is not None:
            pr_url = f"https://github.com/{GITHUB_CONFIG.repo}/pull/{job.pr.number}"
            matrix_id = await self._get_member_id(job.pr.user)
            author = f"@{job.pr.user}"
            if matrix_id is not None:
                author = f'<a href="https://matrix.to/#/{matrix_id}">@{job.pr.user}</a>'
            job_html_description = (
                f'PR <a href="{pr_url}" target="_blank" rel="noreferrer noopener">#{job.pr.number}</a> '
                f'(<a href="{commit_url}" target="_blank" rel="noreferrer noopener">{commit_short}</a>) '
                f"by {author}"
            )
        elif job.is_tag():
            tag_url = f"https://github.com/{GITHUB_CONFIG.repo}/tree/{job.ref[10:]}"
            job_html_description = (
                f'tag <a href="{tag_url}" target="_blank" rel="noreferrer noopener">{job.ref[10:]}</a> '
                f'(<a href="{commit_url}" target="_blank" rel="noreferrer noopener">{commit_short}</a>)'
            )
        elif job.is_branch():
            branch_url = f"https://github.com/{GITHUB_CONFIG.repo}/tree/{job.ref[11:]}"
            job_html_description = (
                f'branch <a href="{branch_url}" target="_blank" rel="noreferrer noopener">{job.ref[11:]}</a> '
                f'(<a href="{commit_url}" target="_blank" rel="noreferrer noopener">{commit_short}</a>)'
            )
        else:
            job_html_description = f'commit <a href="{commit_url}" target="_blank" rel="noreferrer noopener">{commit_short}</a>'

        state_color = "green" if job_state == "PASSED" else "red"
        html_content = (
            f'{emoji} <b><font color="{state_color}">{job_state}</font></b> - {job_html_description}: '
            f'<a href="{job.details_url}" target="_blank" rel="noreferrer noopener">{job.details_url}</a>'
        )
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://matrix.org/_matrix/client/v3/rooms/%21{self.config.room[1:]}"
                    f"/send/m.room.message?access_token={self.config.token}",
                    content=json.dumps(
                        {
                            "msgtype": "m.text",
                            "format": "org.matrix.custom.html",
                            "body": content,
                            "formatted_body": html_content,
                        }
                    ),
                )
                if response.status_code != 200:
                    LOGGER.debug(
                        f"Cannot send message to matrix room '{self.config.room}': {response} {response.json()}"
                    )
                    return
                LOGGER.debug(f"Notification posted on Matrix room '{self.config.room}'")
        except httpx.ReadTimeout as exc:
            LOGGER.warning(f"Failed to post on Matrix room '{self.config.room}': {exc}")


class Notifier:
    config = NOTIFIER_CONFIG
    _notifiers = {
        "mail": MailNotifier(),
        "matrix": MatrixNotifier(),
    }

    async def notify(self, job: MurdockJob, db: Database):
        if job.pr is not None:
            for notifier_type in self.config.pr:
                await self._notifiers[notifier_type].notify(job)
        elif job.ref is not None and job.ref.startswith("refs/"):
            query = JobQueryModel(ref=job.ref, states="passed errored", limit=1)
            last_matching_jobs = await db.find_jobs(query)
            if last_matching_jobs:
                last_matching_job = last_matching_jobs[0]
                LOGGER.debug(
                    f"Last matching job {last_matching_job.uid[:7]} state is {last_matching_job.state}"
                )
                if last_matching_job.state == "passed" and job.state == "passed":
                    LOGGER.debug(
                        f"{job} result still successful, skipping notification"
                    )
                    return
            if job.is_branch():
                for notifier_type in self.config.branch:
                    await self._notifiers[notifier_type].notify(job)
            elif job.is_tag():
                for notifier_type in self.config.tag:
                    await self._notifiers[notifier_type].notify(job)
        else:
            for notifier_type in self.config.commit:
                await self._notifiers[notifier_type].notify(job)
