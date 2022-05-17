import asyncio
import json
import os
import re
import time

from typing import List, Optional, Union

import websockets
from fastapi import WebSocket

from murdock.config import GLOBAL_CONFIG, CI_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.job_containers import MurdockJobList, MurdockJobPool
from murdock.models import (
    JobModel,
    ManualJobBranchParamModel,
    ManualJobTagParamModel,
    ManualJobCommitParamModel,
    PullRequestInfo,
    JobQueryModel,
)
from murdock.github import (
    comment_on_pr,
    fetch_commit_info,
    fetch_branch_info,
    fetch_tag_info,
    fetch_user_login,
    set_commit_status,
    fetch_murdock_config,
)
from murdock.database import Database
from murdock.notify import Notifier


ALLOWED_ACTIONS = [
    "edited",
    "labeled",
    "unlabeled",
    "synchronize",
    "created",
    "closed",
    "opened",
    "reopened",
]


class Murdock:
    def __init__(
        self,
        base_url: str = GLOBAL_CONFIG.base_url,
        repository: Optional[str] = None,
        work_dir: str = GLOBAL_CONFIG.work_dir,
        num_workers: int = GLOBAL_CONFIG.num_workers,
        commit_status_context: str = GLOBAL_CONFIG.commit_status_context,
        cancel_on_update: bool = GLOBAL_CONFIG.cancel_on_update,
        store_stopped_jobs: bool = GLOBAL_CONFIG.store_stopped_jobs,
        enable_notifications: bool = GLOBAL_CONFIG.enable_notifications,
    ):
        self.base_url: str = base_url
        self.repository: Optional[str] = repository
        self.work_dir: str = work_dir
        self.commit_status_context: str = commit_status_context
        self.cancel_on_update: bool = cancel_on_update
        self.store_stopped_jobs: bool = store_stopped_jobs
        self.enable_notifications: bool = enable_notifications
        self.clients: List[WebSocket] = []
        self.num_workers: int = num_workers
        self.queued: MurdockJobList = MurdockJobList()
        self.running: MurdockJobPool = MurdockJobPool(num_workers)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.fasttrack_queue: asyncio.Queue = asyncio.Queue()
        self.db = Database()
        self.notifier = Notifier()

    async def init(self):
        await self.db.init()
        for index in range(self.num_workers):
            asyncio.create_task(
                self.job_processing_task(), name=f"MurdockWorker_{index}"
            )

    async def shutdown(self):
        LOGGER.info("Shutting down Murdock")
        self.db.close()
        for ws in self.clients:
            LOGGER.debug(f"Closing websocket {ws}")
            await ws.close()
        for job in self.queued.jobs:
            LOGGER.debug(f"Canceling {job}")
            job.cancelled = True
        for job in self.running.jobs:
            if job is not None:
                LOGGER.debug(f"Stopping {job}")
                await job.stop()

    async def _process_job(self, job: MurdockJob):
        if job.canceled is True:
            LOGGER.debug(f"Ignoring canceled {job}")
        else:
            LOGGER.info(f"Processing {job} [{asyncio.current_task().get_name()}]")  # type: ignore[union-attr]
            await self.job_prepare(job)
            try:
                await job.exec(self.notify_message_to_clients)
            except Exception as exc:
                LOGGER.warning(f"Build job failed:\n{exc}")
                job.state = "errored"
            await self.job_finalize(job)
            LOGGER.info(f"{job} completed")

    async def job_processing_task(self):
        current_task = asyncio.current_task().get_name()
        while True:
            if self.fasttrack_queue.qsize():
                job = self.fasttrack_queue.get_nowait()
                await self._process_job(job)
                self.fasttrack_queue.task_done()
            else:
                try:
                    job = await self.queue.get()
                    await self._process_job(job)
                    self.queue.task_done()
                except RuntimeError as exc:
                    LOGGER.info(f"Exiting worker {current_task}: {exc}")
                    break

    async def job_prepare(self, job: MurdockJob):
        self.queued.remove(job)
        self.running.add(job)
        job.state = "running"
        LOGGER.debug(f"{job} added to the running jobs")
        job.start_time = time.time()
        await set_commit_status(
            job.commit.sha,
            {
                "state": "pending",
                "context": self.commit_status_context,
                "description": "The job has started",
                "target_url": job.details_url,
            },
        )
        await self.reload_jobs()

    async def job_finalize(self, job: MurdockJob):
        job.stop_time = time.time()
        if "status" in job.status and job.status["status"] == "working":
            job.status["status"] = "finished"
        self.running.remove(job)
        LOGGER.debug(f"{job} removed from running jobs")
        if job.state == "stopped":
            status = {
                "state": "pending",
                "context": self.commit_status_context,
                "target_url": job.details_url,
                "description": "Stopped",
            }
        else:
            job_state = "success" if job.state == "passed" else "failure"
            job_status_desc = "succeeded" if job.state == "passed" else "failed"
            status = {
                "state": job_state,
                "context": self.commit_status_context,
                "description": (
                    f"The job {(job_status_desc)}. " f"runtime: {job.runtime_human}"
                ),
                "target_url": job.details_url,
            }
            if (
                job.pr is not None
                and job.config is not None
                and job.config.pr.enable_comments
            ):
                LOGGER.info(f"Posting comment on PR #{job.pr.number}")
                await comment_on_pr(job)
        await set_commit_status(job.commit.sha, status)
        # Notifications must be called before inserting the job in DB
        # because the logic checks the result of the last matching job in DB.
        if job.state in ["passed", "errored"] and self.enable_notifications is True:
            await self.notifier.notify(job, self.db)
        if job.state in ["passed", "errored"] or (
            job.state == "stopped" and self.store_stopped_jobs
        ):
            await self.db.insert_job(job)
        await self.reload_jobs()

    async def add_job_to_queue(self, job: MurdockJob):
        await set_commit_status(
            job.commit.sha,
            {
                "state": "pending",
                "context": self.commit_status_context,
                "description": "The job has been queued",
                "target_url": self.base_url,
            },
        )
        all_busy = all(running is not None for running in self.running.jobs)
        self.queued.add(job)
        job.state = "queued"
        if all_busy and job.fasttracked:
            self.fasttrack_queue.put_nowait(job)
        else:
            self.queue.put_nowait(job)
        LOGGER.info(f"{job} added to queued jobs")
        await self.reload_jobs()

    async def cancel_queued_jobs_matching(self, job: MurdockJob) -> List[MurdockJob]:
        jobs_to_cancel = []
        if job.pr is not None:
            jobs_to_cancel += self.queued.search_by_pr_number(job.pr.number)
        if job.ref is not None:
            jobs_to_cancel += self.queued.search_by_ref(job.ref)
        for job in jobs_to_cancel:
            await self.cancel_queued_job(job)
        return jobs_to_cancel

    async def cancel_queued_job(self, job: MurdockJob, reload_jobs=False):
        LOGGER.debug(f"Canceling {job}")
        job.canceled = True
        self.queued.remove(job)
        status = {
            "state": "pending",
            "context": self.commit_status_context,
            "target_url": self.base_url,
            "description": "Canceled",
        }
        await set_commit_status(job.commit.sha, status)
        if reload_jobs is True:
            await self.reload_jobs()

    async def stop_running_jobs_matching(self, job: MurdockJob) -> List[MurdockJob]:
        jobs_to_stop = []
        if job.pr is not None:
            jobs_to_stop += self.running.search_by_pr_number(job.pr.number)
        if job.ref is not None:
            jobs_to_stop += self.running.search_by_ref(job.ref)
        for job in jobs_to_stop:
            await self.stop_running_job(job)
        return jobs_to_stop

    async def stop_running_job(self, job: MurdockJob, fail=False) -> MurdockJob:
        LOGGER.debug(f"Stopping {job}")
        if fail is True:
            LOGGER.debug(f"Stopping {job} with errored state")
            job.state = "errored"

        await job.stop()
        return job

    async def disable_jobs_matching(self, job: MurdockJob) -> List[MurdockJob]:
        LOGGER.debug(f"Disable jobs matching {job}")
        disabled_jobs = []
        disabled_jobs += await self.cancel_queued_jobs_matching(job)
        disabled_jobs += await self.stop_running_jobs_matching(job)
        if disabled_jobs:
            await self.reload_jobs()
        return disabled_jobs

    async def update_matching_prs(self, pull_request: PullRequestInfo):
        # Update matching PRs that are queued or running
        matching_jobs = self.queued.search_by_pr_number(pull_request.number)
        matching_jobs += self.running.search_by_pr_number(pull_request.number)
        modified_jobs = 0
        for matching_job in matching_jobs:
            if matching_job.pr is None:
                continue
            if matching_job.pr.labels != pull_request.labels:
                LOGGER.debug(f"Updating {matching_job} labels")
                matching_job.pr.labels = pull_request.labels
                modified_jobs += 1
            if matching_job.pr.title != pull_request.title:
                LOGGER.debug(f"Updating {matching_job} title")
                matching_job.pr.title = pull_request.title
                modified_jobs += 1
            if matching_job.pr.state != pull_request.state:
                LOGGER.debug(f"Updating {matching_job} state")
                matching_job.pr.state = pull_request.state
                modified_jobs += 1
            if matching_job.pr.is_merged != pull_request.is_merged:
                LOGGER.debug(f"Updating {matching_job} merged state")
                matching_job.pr.is_merged = pull_request.is_merged
                modified_jobs += 1

        # Update matching PRs that are already in DB
        modified_jobs += await self.db.update_jobs(
            JobQueryModel(is_pr=True, prnum=pull_request.number),
            "prinfo.title",
            pull_request.title,
        )
        modified_jobs += await self.db.update_jobs(
            JobQueryModel(is_pr=True, prnum=pull_request.number),
            "prinfo.labels",
            pull_request.labels,
        )
        modified_jobs += await self.db.update_jobs(
            JobQueryModel(is_pr=True, prnum=pull_request.number),
            "prinfo.state",
            pull_request.state,
        )
        modified_jobs += await self.db.update_jobs(
            JobQueryModel(is_pr=True, prnum=pull_request.number),
            "prinfo.is_merged",
            pull_request.is_merged,
        )
        if modified_jobs:
            await self.reload_jobs()

    async def restart_job(self, uid: str, token: str) -> Optional[MurdockJob]:
        if (job := await self.db.find_job(uid)) is None:
            return job
        login = await fetch_user_login(token)
        LOGGER.info(f"Restarting {job}")
        config = await fetch_murdock_config(job.commit.sha)
        new_job = MurdockJob(
            job.commit,
            pr=job.pr,
            ref=job.ref,
            config=config,
            trigger="api (restart)",
            triggered_by=login,
            user_env=job.user_env,
        )
        await self.schedule_job(new_job)
        return new_job

    async def handle_skip_job(self, job: MurdockJob) -> bool:
        if (
            job.config is not None
            and job.config.commit is not None
            and any(
                skip_keyword in job.commit.message
                for skip_keyword in job.config.commit.skip_keywords
            )
        ):
            LOGGER.debug(f"Commit message contains skip keywords, skipping {job}")
            await set_commit_status(
                job.commit.sha,
                {
                    "state": "pending",
                    "context": self.commit_status_context,
                    "description": "The job was skipped.",
                },
            )
            return True
        return False

    async def schedule_job(self, job: MurdockJob) -> Optional[MurdockJob]:
        LOGGER.info(f"Scheduling new {job}")
        # Check if the job should be skipped (using keywords in commit message)
        if await self.handle_skip_job(job) is True:
            return None

        if self.cancel_on_update is True:
            # Similar jobs are already queued or running => cancel/stop them
            await self.disable_jobs_matching(job)

        await self.add_job_to_queue(job)
        return job

    async def handle_pull_request_event(self, event: dict):
        if "action" not in event:
            return "Unsupported event"
        action = event["action"]
        if action not in ALLOWED_ACTIONS:
            return f"Unsupported action '{action}'"
        if (
            self.repository is not None
            and event["repository"]["full_name"] != self.repository
        ):
            return "Invalid repo"
        LOGGER.info(f"Handle pull request event '{action}'")
        pr_data = event["pull_request"]
        sender = event["sender"]["login"]
        commit = await fetch_commit_info(pr_data["head"]["sha"])
        if commit is None:
            error_msg = "Cannot fetch commit information"
            LOGGER.error(f"{error_msg}, aborting")
            return error_msg
        config = await fetch_murdock_config(commit.sha)
        pull_request = PullRequestInfo(
            title=pr_data["title"],
            number=pr_data["number"],
            merge_commit=pr_data["merge_commit_sha"],
            user=pr_data["head"]["user"]["login"],
            url=pr_data["_links"]["html"]["href"],
            base_repo=pr_data["base"]["repo"]["clone_url"],
            base_branch=pr_data["base"]["ref"],
            base_commit=pr_data["base"]["sha"],
            base_full_name=pr_data["base"]["repo"]["full_name"],
            mergeable=pr_data["mergeable"] in [True, None],
            labels=sorted([label["name"] for label in pr_data["labels"]]),
            state=pr_data["state"],
            is_merged=pr_data["merged_at"] is not None,
        )

        job = MurdockJob(
            commit,
            pr=pull_request,
            config=config,
            trigger=f"pr ({action})",
            triggered_by=sender,
        )

        # Update matching PRs (queued, running and finished)
        await self.update_matching_prs(pull_request)

        action = event["action"]
        if action == "closed":
            LOGGER.info(f"PR #{pull_request.number} closed, disabling matching jobs")
            await self.disable_jobs_matching(job)
            return

        if CI_CONFIG.ready_label not in pull_request.labels:
            LOGGER.debug(f"'{CI_CONFIG.ready_label}' label not set")
            await self.disable_jobs_matching(job)
            status = {
                "state": "pending",
                "context": self.commit_status_context,
                "target_url": self.base_url,
                "description": f'"{CI_CONFIG.ready_label}" label not set',
            }
            await set_commit_status(job.commit.sha, status)
            return

        if action in ["unlabeled", "edited"]:
            return

        if action == "opened" and CI_CONFIG.ready_label in pull_request.labels:
            # A PR opened with "Ready label" already set will be scheduled via
            # the "labeled" action
            return

        if action == "labeled":
            if event["label"]["name"] != CI_CONFIG.ready_label:
                return
            # Skip already queued jobs
            if event["label"][
                "name"
            ] == CI_CONFIG.ready_label and self.queued.search_by_pr_number(
                pull_request.number
            ):
                return

        await self.schedule_job(job)

    @staticmethod
    def handle_ref(ref: str, rules: List[str]) -> bool:
        return bool(
            "*" in rules
            or ref in rules
            or (rules and any(re.match(expr, ref) is not None for expr in rules))
        )

    async def handle_push_event(self, event: dict):
        if (
            self.repository is not None
            and event["repository"]["full_name"] != self.repository
        ):
            return "Invalid repo"
        sender = event["sender"]["login"]
        ref = event["ref"]
        ref_type, ref_name = ref.split("/", 2)[-2:]
        if event["after"] == "0000000000000000000000000000000000000000":
            LOGGER.debug(
                f"Ref was removed upstream, aborting all jobs related to ref '{ref}'"
            )
            for job in self.running.search_by_ref(ref):
                await self.cancel_queued_job(job, reload_jobs=True)
            for job in self.queued.search_by_ref(ref):
                await self.stop_running_job(job)
            return
        commit = await fetch_commit_info(event["after"])
        if commit is None:
            LOGGER.error("Cannot fetch commit information, aborting")
            return
        config = await fetch_murdock_config(commit.sha)
        if (
            ref_type == "heads"
            and not Murdock.handle_ref(ref_name, config.push.branches)
        ) or (
            ref_type == "tags" and not Murdock.handle_ref(ref_name, config.push.tags)
        ):
            LOGGER.debug(f"Ref '{ref_name}' not accepted for push events")
            return

        LOGGER.info(f"Handle push event on ref '{ref_name}'")
        await self.schedule_job(
            MurdockJob(
                commit, ref=ref, config=config, trigger="push", triggered_by=sender
            )
        )

    def add_ws_client(self, ws: WebSocket):
        if ws not in self.clients:
            self.clients.append(ws)

    def remove_ws_client(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def _send_text_safe(self, client: WebSocket, msg: str):
        try:
            await client.send_text(msg)
        except websockets.exceptions.ConnectionClosedError as exc:
            LOGGER.warning(f"Could send msg to websocket client: {exc}")
            await asyncio.sleep(0.1)

    async def notify_message_to_clients(self, msg: str):
        await asyncio.gather(
            *[self._send_text_safe(client, msg) for client in self.clients]
        )

    async def reload_jobs(self):
        await self.notify_message_to_clients(json.dumps({"cmd": "reload"}))

    def get_queued_jobs(self, query: JobQueryModel = JobQueryModel()) -> List[JobModel]:
        return sorted(
            [
                job.model()
                for job in self.queued.search_with_query(query)
                if query.states is None or "queued" in query.states
            ],
            key=lambda job: job.fasttracked,  # type: ignore[return-value,arg-type]
        )

    def get_running_jobs(
        self, query: JobQueryModel = JobQueryModel()
    ) -> List[JobModel]:
        return [
            job.model()
            for job in self.running.search_with_query(query)
            if query.states is None or "running" in query.states
        ]

    def _remove_job_data(self, uid):
        work_dir = os.path.join(self.work_dir, uid)
        MurdockJob.remove_dir(work_dir)

    async def remove_finished_jobs(self, query: JobQueryModel) -> List[JobModel]:
        query.limit = -1
        jobs_to_remove = await (self.db.find_jobs(query))
        for job in jobs_to_remove:
            self._remove_job_data(job.uid)
        await self.db.delete_jobs(query)
        LOGGER.info(f"{len(jobs_to_remove)} jobs removed")
        await self.reload_jobs()
        return jobs_to_remove

    async def remove_job(self, uid: str) -> Optional[JobModel]:
        if (job := self.queued.search_by_uid(uid)) is not None:
            await self.cancel_queued_job(job, reload_jobs=True)
            return job.model()
        elif (job := self.running.search_by_uid(uid)) is not None:
            await self.stop_running_job(job)
            return job.model()
        elif (jobs := await self.db.find_jobs(JobQueryModel(uid=uid))) and jobs:
            self._remove_job_data(uid)
            await self.db.delete_jobs(JobQueryModel(uid=uid))
            return jobs[0]
        return None

    async def get_jobs(self, query: JobQueryModel = JobQueryModel()) -> List[JobModel]:
        result = self.get_queued_jobs(query)
        result += self.get_running_jobs(query)
        result += await self.db.find_jobs(query)
        return result

    async def get_job(self, uid: str) -> Optional[JobModel]:
        found_job = None
        if (job := self.queued.search_by_uid(uid)) is not None:
            found_job = job.model()
        elif (job := self.running.search_by_uid(uid)) is not None:
            found_job = job.model()
        elif jobs := await self.db.find_jobs(JobQueryModel(uid=uid)):
            found_job = jobs[0]
        return found_job

    async def start_job(
        self,
        ref,
        commit,
        token,
        param: Union[
            ManualJobBranchParamModel, ManualJobTagParamModel, ManualJobCommitParamModel
        ],
    ) -> Optional[JobModel]:
        if commit is None:
            return None

        login = await fetch_user_login(token)

        # Fetch and setup job configuration
        config = await fetch_murdock_config(commit.sha)

        LOGGER.info(f"Schedule manual job for ref '{ref}'")
        job = MurdockJob(
            commit,
            ref=ref,
            config=config,
            trigger="api (manual)",
            triggered_by=login,
            user_env=param.env,
        )
        if param.fasttrack is True:
            job.fasttracked = True
        await self.schedule_job(job)
        return job.model()

    async def start_branch_job(
        self, token, param: ManualJobBranchParamModel
    ) -> Optional[JobModel]:
        LOGGER.debug(f"Starting manual job on branch {param.branch}")
        commit = await fetch_branch_info(param.branch)
        ref = f"refs/heads/{param.branch}"
        return await self.start_job(ref, commit, token, param)

    async def start_tag_job(
        self, token, param: ManualJobTagParamModel
    ) -> Optional[JobModel]:
        LOGGER.debug(f"Starting manual job on tag {param.tag}")
        commit = await fetch_tag_info(param.tag)
        ref = f"refs/tags/{param.tag}"
        return await self.start_job(ref, commit, token, param)

    async def start_commit_job(
        self, token, param: ManualJobCommitParamModel
    ) -> Optional[JobModel]:
        LOGGER.debug(f"Starting manual job on commit {param.sha}")
        commit = await fetch_commit_info(param.sha)
        ref = f"Commit {param.sha}"
        return await self.start_job(ref, commit, token, param)

    async def handle_job_status_data(
        self, uid: str, data: dict
    ) -> Optional[MurdockJob]:
        job = self.running.search_by_uid(uid)
        if job is not None and "status" in data and data["status"]:
            job.status = data["status"]
            if (
                job.config is not None
                and job.config.failfast is True
                and (
                    "failed_jobs" in job.status
                    or "failed_builds" in job.status
                    or "failed_tests" in job.status
                )
            ):
                LOGGER.debug(f"Failfast enabled and failures detected, stopping {job}")
                job = await self.stop_running_job(job, fail=True)
            data.update({"cmd": "status", "uid": job.uid})
            await self.notify_message_to_clients(json.dumps(data))
        return job
