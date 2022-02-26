import json

from email.message import EmailMessage

import httpx
import aiosmtplib

from murdock.database import Database
from murdock.job import MurdockJob
from murdock.log import LOGGER
from murdock.models import JobQueryModel
from murdock.config import MAIL_NOTIFIER_CONFIG, MATRIX_NOTIFIER_CONFIG, NOTIFIER_CONFIG


class MailNotifier:
    config = MAIL_NOTIFIER_CONFIG

    async def notify(self, job: MurdockJob):
        title = f"Murdock job {job.state} - {job.title}"
        content = f"Details: {job.details_url}"

        message = EmailMessage()
        message["From"] = "ci@riot-os.org"
        message["To"] = ";".join(self.config.recipients)
        message["Subject"] = title
        message.set_content(content)

        try:
            await aiosmtplib.send(
                message,
                hostname=self.config.server,
                port=self.config.port,
                use_tls=self.config.use_tls,
                username=self.config.username,
                password=self.config.password,
            )
        except aiosmtplib.errors.SMTPAuthenticationError as exc:
            LOGGER.debug(f"Cannot send email: {exc}")
        except aiosmtplib.errors.SMTPConnectError as exc:
            LOGGER.debug(f"Cannot send email: {exc}")
        else:
            LOGGER.debug(f"Notification email sent to '{self.config.recipients}'")


class MatrixNotifier:
    config = MATRIX_NOTIFIER_CONFIG

    async def notify(self, job: MurdockJob):
        emoji = "&#x274C;" if job.state == "errored" else "&#x2705;"
        content = f"Murdock job on {job.title} {job.state}: {job.details_url}"
        html_content = (
            f"{emoji} Murdock job on {job.title} <b>{job.state}</b>: "
            f'<a href="{job.details_url}" target="_blank" rel="noreferrer noopener">{job.details_url}</a>'
        )
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
            if job.ref.startswith("refs/heads"):
                for notifier_type in self.config.branch:
                    await self._notifiers[notifier_type].notify(job)
            elif job.ref.startswith("refs/tags"):
                for notifier_type in self.config.tag:
                    await self._notifiers[notifier_type].notify(job)
        else:
            for notifier_type in self.config.commit:
                await self._notifiers[notifier_type].notify(job)
