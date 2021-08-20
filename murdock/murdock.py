import asyncio
import json
import re
import time

from datetime import datetime
from datetime import time as dtime
from typing import Optional, List

import motor.motor_asyncio as aiomotor

import httpx

from bson.objectid import ObjectId
from fastapi import WebSocket

from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import PullRequestInfo
from murdock.config import CONFIG


ALLOWED_ACTIONS = [
    "labeled", "unlabeled", "synchronize", "created",
    "closed", "opened", "reopened",
]


class Murdock:

    def __init__(self):
        self.clients : List[WebSocket] = []
        self.queued : List[MurdockJob] = []
        self.num_workers = CONFIG.murdock_num_workers
        self.running_jobs : List[MurdockJob] = [None] * self.num_workers
        self.queue : asyncio.Queue = asyncio.Queue()
        self.fasttrack_queue : asyncio.Queue = asyncio.Queue()
        self.db = None

    async def init(self):
        await self.init_db()
        for index in range(self.num_workers):
            asyncio.create_task(
                self.job_processing_task(), name=f"MurdockWorker_{index}"
        )

    async def init_db(self):
        LOGGER.info("Initializing database connection")
        loop = asyncio.get_event_loop()
        conn = aiomotor.AsyncIOMotorClient(
            f"mongodb://{CONFIG.murdock_db_host}:{CONFIG.murdock_db_port}",
            maxPoolSize=5,
            io_loop=loop
        )
        self.db = conn[CONFIG.murdock_db_name]

    async def shutdown(self):
        LOGGER.info("Shutting down Murdock")
        self.db.client.close()
        for ws in self.clients:
            LOGGER.debug(f"Closing websocket {ws}")
            ws.close()
        for job in self.queued:
            LOGGER.debug(f"Canceling job {job}")
            job.cancelled = True
        for job in self.running_jobs:
            if job is not None:
                LOGGER.debug(f"Stopping job {job}")
                await job.stop()

    async def _process_job(self, job: MurdockJob):
        if job.canceled is True:
            LOGGER.debug(f"Ignoring canceled job {job}")
        else:
            LOGGER.info(
                f"Processing job {job} "
                f"[{asyncio.current_task().get_name()}]"
            )
            await self.job_prepare(job)
            try:
                await job.execute()
            except Exception as exc:
                LOGGER.warning(f"Build job failed:\n{exc}")
                job.result = "errored"
            await self.job_finalize(job)
            LOGGER.info(f"Job {job} completed")

    async def job_processing_task(self):
        while True:
            if self.fasttrack_queue.qsize():
                job = self.fasttrack_queue.get_nowait()
                await self._process_job(job)
                self.fasttrack_queue.task_done()
            else:
                job = await self.queue.get()
                await self._process_job(job)
                self.queue.task_done()

    async def job_prepare(self, job: MurdockJob):
        if job in self.queued:
            self.queued.remove(job)
        self.add_to_running_jobs(job)
        job.start_time = time.time()
        await self.set_pull_request_status(
            job.pr.commit,
            {
                "state": "pending",
                "context": "Murdock",
                "description": "The build has started",
                "target_url": CONFIG.murdock_base_url,
            }
        )
        await self.reload_jobs()

    async def job_finalize(self, job: MurdockJob):
        job.stop_time = time.time()
        if job.status["status"] == "working":
            job.status["status"] = "finished"
        self.remove_from_running_jobs(job)
        if job.result != "stopped":
            job_state = "success" if job.result == "passed" else "failure"
            job_status_desc = (
                "succeeded" if job.result == "passed" else "failed"
            )
            await self.set_pull_request_status(
                job.pr.commit,
                {
                    "state": job_state,
                    "context": "Murdock",
                    "description": (
                        f"The build {(job_status_desc)}. "
                        f"runtime: {job.runtime_human}"
                    )
                }
            )
            await self.db.job.insert_one(MurdockJob.to_db_entry(job))
        await self.reload_jobs()

    async def add_job_to_queue(self, job: MurdockJob, reload_jobs=True):
        all_busy = all(running is not None for running in self.running_jobs)
        if all_busy and job.fasttracked:
            self.fasttrack_queue.put_nowait(job)
        else:
            self.queue.put_nowait(job)
        self.queued.append(job)
        LOGGER.info(f"Job {job} added to queued jobs")
        await self.set_pull_request_status(
            job.pr.commit,
            {
                "state": "pending",
                "context": "Murdock",
                "description": "The build has been queued",
                "target_url": CONFIG.murdock_base_url,
            }
        )
        if reload_jobs is True:
            await self.reload_jobs()

    def find_queued_job_matching_commit(self, commit):
        for queued_job in self.queued:
            if queued_job.pr.commit == commit:
                return queued_job

    def job_matching_pr_is_queued(self, prnum: int):
        return any(
            [(queued.pr.number ==  prnum) for queued in self.queued]
        )

    def cancel_queued_job(self, job: MurdockJob):
        for queued_job in self.queued:
            if queued_job.pr.number == job.pr.number:
                LOGGER.debug(f"Canceling job {queued_job}")
                queued_job.canceled = True

    async def cancel_queued_job_with_commit(self, commit: str):
        for queued_job in self.queued:
            if queued_job.pr.commit == commit:
                LOGGER.debug(f"Canceling job {queued_job}")
                queued_job.canceled = True
                status = {
                    "state":"pending",
                    "context": "Murdock",
                    "target_url": CONFIG.murdock_base_url,
                    "description": "Canceled",
                }
                await self.set_pull_request_status(commit, status)
                await self.reload_jobs()
                return queued_job

    def add_to_running_jobs(self, job: MurdockJob):
        for index, running in enumerate(self.running_jobs):
            if running is None:
                self.running_jobs[index] = job
                LOGGER.debug(f"{job} added to the running jobs")
                break

    def job_running(self, commit: str) -> Optional[MurdockJob]:
        for running in self.running_jobs:
            if running is not None and running.pr.commit == commit:
                return running
        return None

    def job_matching_pr_is_running(self, prnum: int) -> bool:
        return any(
            [
                running is not None and running.pr.number == prnum
                for running in self.running_jobs
            ]
        )

    def remove_from_running_jobs(self, job: MurdockJob):
        for index, running in enumerate(self.running_jobs):
            if running is not None and running.pr.commit == job.pr.commit:
                self.running_jobs[index] = None
                LOGGER.debug(f"{job} removed from the running jobs")
                break

    async def disable_jobs_matching_pr(self, prnum: int, description=None):
        disabled_jobs = []
        for job in self.running_jobs:
            if job.pr.number == prnum:
                await self.stop_job(job)
                disabled_jobs.append(job)
        for job in self.queued:
            if job.pr.number == prnum:
                self.cancel_queued_job(job)
                disabled_jobs.append(job)
        LOGGER.debug(f"All jobs matching {job} disabled")
        for job in disabled_jobs:
            self.queued.remove(job)
            status = {
                "state":"pending",
                "context": "Murdock",
                "target_url": CONFIG.murdock_base_url,
            }
            if description is not None:
                status.update({
                    "description": description,
                })
            await self.set_pull_request_status(job.pr.commit, status)
        await self.reload_jobs()

    async def stop_running_jobs_matching_pr(self, prnum: int):
        for running in self.running_jobs:
            if running is not None and running.pr.number == prnum:
                LOGGER.debug(f"Stopping job {running}")
                await running.stop()

    async def stop_running_job(self, commit: str):
        for running in self.running_jobs:
            if running is not None and running.pr.commit == commit:
                LOGGER.debug(f"Stopping job {running}")
                await running.stop()
                status = {
                    "state":"pending",
                    "context": "Murdock",
                    "target_url": CONFIG.murdock_base_url,
                    "description": "Stopped",
                }
                await self.set_pull_request_status(commit, status)
                return running

    async def stop_job(self, job: MurdockJob):
        await self.stop_running_job(job.pr.commit)

    async def restart_job(self, uid: str) -> MurdockJob:
        entry = await self.db.job.find({"uid": uid}).to_list(length=1)
        if not entry:
            LOGGER.warning(f"Cannot find job matching uid '{uid}'")
            return

        job = MurdockJob(PullRequestInfo(**entry[0]["prinfo"]))
        LOGGER.info(f"Restarting job {job}")
        await self.schedule_job(job)
        return job

    async def schedule_job(self, job: MurdockJob) -> MurdockJob:
        if job in self.queued or job in self.running_jobs:
            LOGGER.debug(f"job {job} is already handled, ignoring")
            return

        LOGGER.info(f"Scheduling new job {job}")
        if  (
            CONFIG.ci_cancel_on_update and
            self.job_matching_pr_is_queued(job.pr.number)
        ):
            LOGGER.debug(f"Re-queue job {job}")
            # Similar job is already queued => cancel it and queue the new one
            self.cancel_queued_job(job)
            await self.add_job_to_queue(job)
        elif (
            CONFIG.ci_cancel_on_update and
            self.job_matching_pr_is_running(job.pr.number)
        ):
            # Similar job is already running => stop it and queue the new one
            LOGGER.debug(f"{job} job is already running")
            await self.stop_running_jobs_matching_pr(job.pr.number)
            await self.add_job_to_queue(job, reload_jobs=False)
        else:
            await self.add_job_to_queue(job)
        return job

    async def fetch_commit_message(self, commit: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{CONFIG.github_repo}"
                f"/commits/{commit}",
                headers={
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            if response.status_code != 200:
                return ""
            return response.json()["commit"]["message"]

    async def handle_pull_request_event(self, event: dict):
        if "action" not in event:
            return "Unsupported event"
        action = event["action"]
        if action not in ALLOWED_ACTIONS:
            return f"Unsupported action '{action}'"
        LOGGER.info(f"Handle pull request event '{action}'")
        pr_data = event["pull_request"]
        commit_message = (
            await self.fetch_commit_message(pr_data["head"]["sha"])
        )
        pull_request = PullRequestInfo(
            title=pr_data["title"],
            number=pr_data["number"],
            merge_commit=pr_data["merge_commit_sha"],
            branch=pr_data["head"]["ref"],
            commit=pr_data["head"]["sha"],
            commit_message=commit_message,
            user=pr_data["head"]["user"]["login"],
            url=pr_data["_links"]["html"]["href"],
            base_repo=pr_data["base"]["repo"]["clone_url"],
            base_branch=pr_data["base"]["ref"],
            base_commit=pr_data["base"]["sha"],
            base_full_name=pr_data["base"]["repo"]["full_name"],
            mergeable=pr_data["mergeable"] in [True, None],
            labels=sorted(
                [label["name"] for label in pr_data["labels"]]
            )
        )

        job = MurdockJob(pull_request)
        action = event["action"]
        if action == "closed":
            if (
                self.job_matching_pr_is_running(job.pr.number) or
                self.job_matching_pr_is_running(job.pr.number)
            ):
                await self.disable_jobs_matching_pr(job.pr.number)
            return

        if any(
            re.match(rf"^({'|'.join(CONFIG.ci_skip_keywords)})$", line)
            for line in commit_message.split('\n')
        ):
            LOGGER.debug(
                f"Commit message contains skip keywords, skipping job {job}"
            )
            await self.set_pull_request_status(
                job.pr.commit,
                {
                    "state": "pending",
                    "context": "Murdock",
                    "description": "The build was skipped."
                }
            )
            return

        if action == "labeled":
            label = event["label"]["name"]
            if CONFIG.ci_ready_label not in pull_request.labels:
                return
            elif (
                label == CONFIG.ci_ready_label and
                (job in self.queued or job in self.running_jobs)
            ):
                LOGGER.debug(f"job {job} is already handled, ignoring")
                return
            elif (
                label != CONFIG.ci_ready_label and
                (queued_job := self.find_queued_job_matching_commit(job.pr.commit)) is not None
            ):
                LOGGER.debug(
                    f"Updating queued job {queued_job} with new label '{label}'"
                )
                queued_job.pr.labels.append(label)
                return

        if CONFIG.ci_ready_label not in pull_request.labels:
            LOGGER.debug(f"'{CONFIG.ci_ready_label}' label not set")
            if (
                self.job_matching_pr_is_queued(job.pr.number) or
                self.job_matching_pr_is_running(job.pr.number)
            ):
                await self.disable_jobs_matching_pr(
                    job.pr.number,
                    description=f"\"{CONFIG.ci_ready_label}\" label not set",
                )
            return

        if (
            action == "unlabeled" and
            (queued_job := self.find_queued_job_matching_commit(job.pr.commit)) is not None
        ):
            label = event["label"]["name"]
            LOGGER.debug(
                f"Removing '{label}' from queued job {queued_job}"
            )
            queued_job.pr.labels.remove(label)

        await self.schedule_job(job)

    async def set_pull_request_status(self, commit: str, status: dict):
        LOGGER.debug(
            f"Setting commit {commit[0:7]} status to '{status['description']}'"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{CONFIG.github_repo}/statuses/{commit}",
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "Authorization": f"token {CONFIG.github_api_token}"
                }
                , data=json.dumps(status)
            )
            if response.status_code != 201:
                LOGGER.warning(f"{response}: {response.json()}")

    def add_ws_client(self, ws: WebSocket):
        if ws not in self.clients:
            self.clients.append(ws)

    def remove_ws_client(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def _broadcast_message(self, msg: str):
        await asyncio.gather(
            *[client.send_text(msg) for client in self.clients]
        )

    async def reload_jobs(self):
        await self._broadcast_message(json.dumps({"cmd": "reload"}))

    def get_queued_jobs(self) -> list:
        queued = sorted(
            [
                job.queued_model()
                for job in self.queued if job.canceled is False
            ],
            reverse=True, key=lambda job: job["since"]
        )
        return sorted(queued, key=lambda job: job["fasttracked"])

    def get_running_jobs(self) -> list:
        return sorted(
            [
                job.running_model()
                for job in self.running_jobs if job is not None
            ], reverse=True, key=lambda job: job["since"]
        )

    async def get_finished_jobs(
        self,
        limit: int,
        job_id: Optional[str] = None,
        prnum: Optional[int] = None,
        user: Optional[str] = None,
        result: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> list:
        query = {}
        if job_id is not None:
            query.update({"_id": ObjectId(job_id)})
        if prnum is not None:
            query.update({"prnum": str(prnum)})
        if user is not None:
            query.update({"user": user})
        if result in ["errored", "passed"]:
            query.update({"result": result})
        if after is not None:
            date = datetime.strptime(after, "%Y-%m-%d")
            query.update({"since": {"$gte": date.timestamp()}})
        if before is not None:
            date = datetime.combine(
                datetime.strptime(before, "%Y-%m-%d"),
                dtime(hour=23, minute=59, second=59, microsecond=999)
            )
            if "since" in query:
                query["since"].update({"$lte": date.timestamp()})
            else:
                query.update({"since": {"$lte": date.timestamp()}})
        finished = await (
            self.db.job
            .find(query)
            .sort("since", -1)
            .to_list(length=limit)
        )
        return [MurdockJob.from_db_entry(job) for job in finished]

    async def remove_finished_jobs(self, before: str) -> int:
        date = datetime.strptime(before, "%Y-%m-%d")
        jobs_before = await self.db.job.count_documents({})
        query = {"since": {"$gte": date.timestamp()}}
        jobs_count = await self.db.job.count_documents(query)
        jobs_to_remove = await (
            self.db.job
            .find(query)
            .sort("since", -1)
            .to_list(length=jobs_count)
        )
        for job_data in jobs_to_remove:
            MurdockJob.remove_dir(job_data["work_dir"])
        await self.db.job.delete_many(query)
        jobs_removed = jobs_before - (await self.db.job.count_documents({}))
        LOGGER.info(f"{jobs_removed} jobs removed (before {before})")
        await self.reload_jobs()
        return [MurdockJob.from_db_entry(job) for job in jobs_to_remove]


    async def get_jobs(self, limit: int) -> dict:
        finished = await self.get_finished_jobs(limit)
        return {
            "queued": self.get_queued_jobs(),
            "building": self.get_running_jobs(),
            "finished": finished,
        }

    async def handle_commit_status_data(
        self, commit: str, data: dict
    ) -> MurdockJob:
        job = self.job_running(commit)
        if job is not None and "status" in data and data["status"]:
            job.status = data["status"]
            data.update({"cmd": "status", "commit": commit})
            await self._broadcast_message(json.dumps(data))
        return job
