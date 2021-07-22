import asyncio
import json
import sys
import time

from collections import namedtuple

import aiohttp
from aiohttp import web

from gidgethub import aiohttp as gh_aiohttp

from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.config import (
    check_config, CONFIG_MSG,
    CI_FASTTRACK_LABELS, CI_READY_LABEL, CI_CANCEL_ON_UPDATE,
    GITHUB_API_TOKEN, GITHUB_API_USER, GITHUB_REPO,
    MURDOCK_BASE_URL, MURDOCK_NUM_WORKERS,
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
        self.finished = []
        self.queued = []
        self.running_jobs = [None] * MURDOCK_NUM_WORKERS
        self.queue = asyncio.Queue()
        self.fasttrack_queue = asyncio.Queue()

    def init(self):
        LOGGER.debug(CONFIG_MSG)
        check_msg = check_config()
        if check_msg:
            LOGGER.error(f"Error: {check_msg}")
            sys.exit(1)
        for index in range(MURDOCK_NUM_WORKERS):
            asyncio.create_task(
                self.job_processing_task(), name=f"MurdockWorker_{index}"
            )

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
        await self.reload_prs()

    async def job_finalize(self, job):
        job.stop_time = time.time()
        self.remove_from_running_jobs(job)
        if job.result != "killed":
            self.finished.append(job)
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
        await self.reload_prs()

    def cancel_queued_job(self, job):
        for queued_job in self.queued:
            if queued_job.pr.number == job.pr.number:
                LOGGER.debug(f"Canceling job {job}")
                queued_job.canceled = True

    def remove_job(self, job):
        if self.job_is_running(job):
            self.kill_matching_job(job)
            return True
        if self.job_is_queued(job):
            self.cancel_queued_job(job)
            return True
        return False

    def kill_matching_job(self, job):
        matching_job = self.get_matching_job_running(job.pr.number)
        if matching_job is not None:    
            LOGGER.debug(f"Killing job {matching_job}")
            matching_job.kill()

    async def add_job_to_queue(self, job, reload_prs=True):
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
        if reload_prs is True:
            await self.reload_prs()

    def job_is_queued(self, job):
        return any(
            [(queued.pr.number ==  job.pr.number) for queued in self.queued]
        )

    def add_to_running_jobs(self, job):
        for index, running in enumerate(self.running_jobs):
            if running is None:
                self.running_jobs[index] = job
                LOGGER.debug(
                    f"{job} added to the running jobs {self.running_jobs}"
                )
                break

    def remove_from_running_jobs(self, job):
        for index, running in enumerate(self.running_jobs):
            if running is not None and running.pr.number == job.pr.number:
                self.running_jobs[index] = None
                LOGGER.debug(
                    f"{job} removed from the running jobs {self.running_jobs}"
                )
                break

    def job_is_running(self, job):
        return any(
            running is not None and running.pr.number == job.pr.number
            for running in self.running_jobs
        )

    def get_matching_job_running(self, prnum):
        for running in self.running_jobs:
            if running is not None and running.pr.number == prnum:
                return running
        return None

    async def handle_pull_request_event(self, event):
        if "action" not in event.data:
            return web.HTTPBadRequest(reason=f"Unsupported event")
        action = event.data["action"]
        if event.data["action"] not in ALLOWED_ACTIONS:
            return web.HTTPBadRequest(reason=f"Unsupported action '{action}'")
        pr_data = event.data["pull_request"]
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
        action = event.data["action"]
        if action == "closed":
            if self.remove_job(job):
                await self.reload_prs()
            await self.set_pull_request_status(
                job.pr.commit, {"state":"pending"}
            )
            return web.HTTPAccepted()

        if (
            action == "labeled" and
            event.data["label"]["name"] == CI_READY_LABEL and
            CI_READY_LABEL in pull_request.labels and
            (self.job_is_running(job) or self.job_is_queued(job))
        ):
            LOGGER.debug("job is already handled, ignoring")
            return web.HTTPAccepted()

        if CI_READY_LABEL not in pull_request.labels:
            LOGGER.debug("'CI: ready for build' label not set")
            if self.remove_job(job):
                LOGGER.debug(f"job '{job} removed")
                await self.reload_prs()
            await self.set_pull_request_status(
                job.pr.commit,
                {
                    "state":"pending",
                    "context": "Murdock",
                    "description": f"\"{CI_READY_LABEL}\" label not set",
                    "target_url": MURDOCK_BASE_URL,
                }
            )
            return web.HTTPAccepted()

        LOGGER.info(f"Handling new job {job}")
        handle_cancel_on_update = (
            MURDOCK_NUM_WORKERS == 1 and CI_CANCEL_ON_UPDATE
        )
        if  handle_cancel_on_update and self.job_is_queued(job):
            LOGGER.debug(f"Re-queue job {job}")
            # Similar job is already queued => cancel it and queue the new one
            self.cancel_queued_job(job)
            await self.add_job_to_queue(job)
        elif handle_cancel_on_update and self.job_is_running(job):
            # Similar job is already running => stop it and queue the new one
            self.kill_matching_job(job)
            await self.add_job_to_queue(job, reload_prs=False)
        else:
            await self.add_job_to_queue(job)

        return web.HTTPAccepted()

    async def set_pull_request_status(self, commit, status):
        LOGGER.debug(
            f"Setting commit {commit[0:7]} status to '{status['description']}'"
        )
        async with aiohttp.ClientSession() as session:
            gh = gh_aiohttp.GitHubAPI(
                session, GITHUB_API_USER, oauth_token=GITHUB_API_TOKEN
            )
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
            *[client.send_str(msg) for client in self.clients]
        )

    async def reload_prs(self):
        await self._broadcast_message(json.dumps({"cmd": "reload_prs"}))

    def pulls(self):
        _queued = sorted(
            [
                {
                    "title" : job.pr.title,
                    "user" : job.pr.user,
                    "url" : job.pr.url,
                    "commit" : job.pr.commit,
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
                    "since" : job.start_time,
                    "status": job.status,
                }
                for job in self.running_jobs if job is not None
            ], reverse=True, key=lambda job: job["since"]
        )
        finished = sorted(
            [
                {
                    "title" : job.pr.title,
                    "user" : job.pr.user,
                    "url" : job.pr.url,
                    "commit" : job.pr.commit,
                    "since" : job.start_time,
                    "runtime": job.runtime,
                    "result": job.result,
                    "output_url": job.output_url,
                    "status": job.status,
                }
                for job in self.finished
            ], reverse=True, key=lambda job: job["since"]
        )

        return {
            "queued": queued,
            "building": building,
            "finished": finished
        }

    async def handle_ctrl_data(self, data):
        prnum = data["prnum"]
        matching_job = self.get_matching_job_running(prnum)
        if matching_job is not None and "status" in data and data["status"]:
            matching_job.status = data["status"]
            await self._broadcast_message(json.dumps(data))
