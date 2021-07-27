import asyncio
import json
import sys
import time

from collections import namedtuple

import motor.motor_asyncio as aiomotor

import httpx
import gidgethub.httpx

from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.config import (
    check_config, CONFIG_MSG,
    CI_FASTTRACK_LABELS, CI_READY_LABEL, CI_CANCEL_ON_UPDATE,
    GITHUB_API_TOKEN, GITHUB_API_USER, GITHUB_REPO,
    MURDOCK_BASE_URL, MURDOCK_NUM_WORKERS,
    MURDOCK_DB_HOST, MURDOCK_DB_PORT, MURDOCK_DB_NAME
)

ALLOWED_ACTIONS = [
    "labeled", "unlabeled", "synchronize", "created",
    "closed", "opened", "reopened",
]

PullRequestInfo = namedtuple(
    "PullRequestInfo",
    [
        "title",
        "number",
        "merge_commit",
        "branch",
        "commit",
        "user",
        "url",
        "base_repo",
        "base_branch",
        "base_commit",
        "base_full_name",
        "mergeable",
        "labels",
    ]
)


class Murdock:

    def __init__(self):
        self.clients = []
        self.queued = []
        self.running_jobs = [None] * MURDOCK_NUM_WORKERS
        self.queue = asyncio.Queue()
        self.fasttrack_queue = asyncio.Queue()
        self.db = None

    async def init(self):
        LOGGER.debug(CONFIG_MSG)
        check_msg = check_config()
        if check_msg:
            LOGGER.error(f"Error: {check_msg}")
            sys.exit(1)
        await self.init_db()
        for index in range(MURDOCK_NUM_WORKERS):
            asyncio.create_task(
                self.job_processing_task(), name=f"MurdockWorker_{index}"
        )

    async def init_db(self):
        LOGGER.info("Initializing database connection")
        loop = asyncio.get_event_loop()
        conn = aiomotor.AsyncIOMotorClient(
            f"mongodb://{MURDOCK_DB_HOST}:{MURDOCK_DB_PORT}",
            maxPoolSize=5,
            io_loop=loop
        )
        self.db = conn[MURDOCK_DB_NAME]

    async def _process_job(self, job):
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

    async def job_prepare(self, job):
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
                "target_url": MURDOCK_BASE_URL,
            }
        )
        await self.reload_jobs()

    async def job_finalize(self, job):
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

    async def add_job_to_queue(self, job, reload_jobs=True):
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
                "target_url": MURDOCK_BASE_URL,
            }
        )
        if reload_jobs is True:
            await self.reload_jobs()

    def job_matching_pr_is_queued(self, prnum):
        return any(
            [(queued.pr.number ==  prnum) for queued in self.queued]
        )

    def cancel_queued_job(self, job):
        for queued_job in self.queued:
            if queued_job.pr.number == job.pr.number:
                LOGGER.debug(f"Canceling job {job}")
                queued_job.canceled = True

    def add_to_running_jobs(self, job):
        for index, running in enumerate(self.running_jobs):
            if running is None:
                self.running_jobs[index] = job
                LOGGER.debug(
                    f"{job} added to the running jobs {self.running_jobs}"
                )
                break

    def job_running(self, commit):
        for running in self.running_jobs:
            if running is not None and running.pr.commit == commit:
                return running
        return None

    def job_matching_pr_is_running(self, prnum):
        return any(
            [
                running is not None and running.pr.number == prnum
                for running in self.running_jobs
            ]
        )

    def remove_from_running_jobs(self, job):
        for index, running in enumerate(self.running_jobs):
            if running is not None and running.pr.commit == job.pr.commit:
                self.running_jobs[index] = None
                LOGGER.debug(
                    f"{job} removed from the running jobs {self.running_jobs}"
                )
                break

    async def disable_jobs_matching_pr(self, prnum, description=None):
        disabled_jobs = []
        for job in self.running_jobs:
            if self.job_matching_pr_is_running(prnum):
                await self.stop_job(job)
                disabled_jobs.append(job)
        for job in self.queued:
            if self.job_matching_pr_is_queued(prnum):
                self.cancel_queued_job(job)
                disabled_jobs.append(job)
        LOGGER.debug(f"All jobs matching {job} disabled")
        for job in disabled_jobs:
            status = {
                "state":"pending",
                "context": "Murdock",
                "target_url": MURDOCK_BASE_URL,
            }
            if description is not None:
                status.update({
                    "description": description,
                })
            await self.set_pull_request_status(job.pr.commit, status)
        await self.reload_jobs()

    async def stop_running_jobs_matching_pr(self, prnum):
        for running in self.running_jobs:
            if running is not None and running.pr.number == prnum:
                LOGGER.debug(f"Stopping job {running}")
                await running.stop()

    async def stop_job(self, job):
        for running in self.running_jobs:
            if running is not None and running.pr.commit == job.pr.commit:
                LOGGER.debug(f"Stopping job {running}")
                await running.stop()

    async def handle_pull_request_event(self, event):
        if "action" not in event:
            return "Unsupported event"
        action = event["action"]
        if action not in ALLOWED_ACTIONS:
            return f"Unsupported action '{action}'"
        pr_data = event["pull_request"]
        pull_request = PullRequestInfo(
            title=pr_data["title"],
            number=str(pr_data["number"]),
            merge_commit=pr_data["merge_commit_sha"],
            branch=pr_data["head"]["ref"],
            commit=pr_data["head"]["sha"],
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

        fasttrack_allowed = any(
            label in CI_FASTTRACK_LABELS for label in pull_request.labels
        )
        job = MurdockJob(pull_request, fasttracked=fasttrack_allowed)
        action = event["action"]
        if action == "closed":
            if (
                self.job_matching_pr_is_running(job.pr.number) or
                self.job_matching_pr_is_running(job.pr.number)
            ):
                await self.disable_jobs_matching_pr(job.pr.number)
            return

        if (
            action == "labeled" and
            event["label"]["name"] == CI_READY_LABEL and
            CI_READY_LABEL in pull_request.labels and
            (job in self.queued or job in self.running_jobs)
        ):
            LOGGER.debug(f"job {job} is already handled, ignoring")
            return

        if CI_READY_LABEL not in pull_request.labels:
            LOGGER.debug(f"'{CI_READY_LABEL}' label not set")
            if (
                self.job_matching_pr_is_queued(job.pr.number) or
                self.job_matching_pr_is_running(job.pr.number)
            ):
                await self.disable_jobs_matching_pr(
                    job.pr.number,
                    description=f"\"{CI_READY_LABEL}\" label not set",
                )
            return

        LOGGER.info(f"Handling new job {job}")
        if  (
            CI_CANCEL_ON_UPDATE and
            self.job_matching_pr_is_queued(job.pr.number)
        ):
            LOGGER.debug(f"Re-queue job {job}")
            # Similar job is already queued => cancel it and queue the new one
            self.cancel_queued_jobs_matching_pr(job.pr.number)
            await self.add_job_to_queue(job)
        elif (
            CI_CANCEL_ON_UPDATE and
            self.job_matching_pr_is_running(job.pr.number)
        ):
            # Similar job is already running => stop it and queue the new one
            LOGGER.debug(f"{job} job is already running")
            await self.stop_running_jobs_matching_pr(job.pr.number)
            await self.add_job_to_queue(job, reload_jobs=False)
        else:
            await self.add_job_to_queue(job)

    async def set_pull_request_status(self, commit, status):
        LOGGER.debug(
            f"Setting commit {commit[0:7]} status to '{status['description']}'"
        )
        async with httpx.AsyncClient() as client:
            gh = gidgethub.httpx.GitHubAPI(client, GITHUB_API_USER,
                                           oauth_token=GITHUB_API_TOKEN)
            await gh.post(
                f"/repos/{GITHUB_REPO}/statuses/{commit}", data=status
            )

    def add_ws_client(self, ws):
        if ws not in self.clients:
            self.clients.append(ws)

    def remove_ws_client(self, ws):
        if ws in self.clients:
            self.clients.remove(ws)

    async def _broadcast_message(self, msg):
        await asyncio.gather(
            *[client.send_text(msg) for client in self.clients]
        )

    async def reload_jobs(self):
        await self._broadcast_message(json.dumps({"cmd": "reload"}))

    async def jobs(self, max_length):
        _queued = sorted(
            [
                {
                    "title" : job.pr.title,
                    "user" : job.pr.user,
                    "url" : job.pr.url,
                    "commit" : job.pr.commit,
                    "prnum": job.pr.number,
                    "since" : job.start_time,
                    "fasttracked": job.fasttracked,
                }
                for job in self.queued if job.canceled is False
            ],
            reverse=True, key=lambda job: job["since"]
        )
        queued = sorted(_queued, key=lambda job: job["fasttracked"])
        building = sorted(
            [
                {
                    "title" : job.pr.title,
                    "user" : job.pr.user,
                    "url" : job.pr.url,
                    "commit" : job.pr.commit,
                    "prnum": job.pr.number,
                    "since" : job.start_time,
                    "status": job.status,
                }
                for job in self.running_jobs if job is not None
            ], reverse=True, key=lambda job: job["since"]
        )
        finished = await (
            self.db.job
            .find()
            .sort("since", -1)
            .to_list(length=max_length)
        )

        return {
            "queued": queued,
            "building": building,
            "finished": [
                MurdockJob.from_db_entry(job) for job in finished
            ]
        }

    async def pull_jobs(self, prnum):
        jobs = await (
            self.db.job
            .find({"prnum": prnum})
            .sort("since", -1)
            .to_list(length=None)
        )
        return [MurdockJob.from_db_entry(job) for job in jobs]

    async def handle_commit_status_data(self, data):
        commit = data["commit"]
        job = self.job_running(commit)
        if job is not None and "status" in data and data["status"]:
            job.status = data["status"]
            data.update({"cmd": "status"})
            await self._broadcast_message(json.dumps(data))
