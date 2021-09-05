import asyncio
import json
import os
import re
import time

from typing import List

from fastapi import WebSocket

from murdock.config import GLOBAL_CONFIG, CI_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.job_containers import MurdockJobList, MurdockJobPool
from murdock.models import (
    CategorizedJobsModel, FinishedJobModel, JobModel, PullRequestInfo,
    JobQueryModel
)
from murdock.github import (
    comment_on_pr, fetch_commit_info, set_commit_status, fetch_murdock_config
)
from murdock.database import Database


ALLOWED_ACTIONS = [
    "labeled", "unlabeled", "synchronize", "created",
    "closed", "opened", "reopened",
]


class Murdock:

    def __init__(self):
        self.clients : List[WebSocket] = []
        self.num_workers = GLOBAL_CONFIG.num_workers
        self.queued : MurdockJobList = MurdockJobList()
        self.active : MurdockJobPool = MurdockJobPool(self.num_workers)
        self.queue : asyncio.Queue = asyncio.Queue()
        self.fasttrack_queue : asyncio.Queue = asyncio.Queue()
        self.db = Database()


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
            ws.close()
        for job in self.queued.jobs:
            LOGGER.debug(f"Canceling job {job}")
            job.cancelled = True
        for job in self.active.jobs:
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
        if job in self.queued.jobs:
            self.queued.remove(job)
        self.active.add(job)
        LOGGER.debug(f"{job} added to the active jobs")
        job.start_time = time.time()
        await set_commit_status(
            job.commit.sha,
            {
                "state": "pending",
                "context": "Murdock",
                "description": "The build has started",
                "target_url": GLOBAL_CONFIG.base_url,
            }
        )
        await self.reload_jobs()

    async def job_finalize(self, job: MurdockJob):
        job.stop_time = time.time()
        if job.status["status"] == "working":
            job.status["status"] = "finished"
        self.active.remove(job)
        LOGGER.debug(f"{job} removed from active jobs")
        if job.result != "stopped":
            job_state = "success" if job.result == "passed" else "failure"
            job_status_desc = (
                "succeeded" if job.result == "passed" else "failed"
            )
            await set_commit_status(
                job.commit.sha,
                {
                    "state": job_state,
                    "context": "Murdock",
                    "description": (
                        f"The build {(job_status_desc)}. "
                        f"runtime: {job.runtime_human}"
                    )
                }
            )
            if job.pr is not None and job.config.pr.enable_comments:
                LOGGER.info(f"Posting comment on PR #{job.pr.number}")
                await comment_on_pr(job)
            await self.db.insert_job(job)
        await self.reload_jobs()

    def sha_is_handled(self, sha : str) -> bool:
        return (
            self.queued.search_by_commit_sha(sha) is not None or
            self.active.search_by_commit_sha(sha) is not None
        )

    def has_matching_jobs(self, job: MurdockJob) -> bool:
        return (
            self.queued.search_matching(job) or
            self.active.search_matching(job)
        )

    async def add_job_to_queue(self, job: MurdockJob, reload_jobs=True):
        all_busy = all(active is not None for active in self.active.jobs)
        if all_busy and job.fasttracked:
            self.fasttrack_queue.put_nowait(job)
        else:
            self.queue.put_nowait(job)
        self.queued.add(job)
        LOGGER.info(f"Job {job} added to queued jobs")
        await set_commit_status(
            job.commit.sha,
            {
                "state": "pending",
                "context": "Murdock",
                "description": "The build has been queued",
                "target_url": GLOBAL_CONFIG.base_url,
            }
        )
        if reload_jobs is True:
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

    async def cancel_queued_job_with_commit(self, commit: str):
        if (job := self.queued.search_by_commit_sha(commit)) is None:
            return
        await self.cancel_queued_job(job)
        await self.reload_jobs()
        return job

    async def cancel_queued_job(self, job: MurdockJob):
        LOGGER.debug(f"Canceling job {job}")
        job.canceled = True
        self.queued.remove(job)
        status = {
            "state":"pending",
            "context": "Murdock",
            "target_url": GLOBAL_CONFIG.base_url,
            "description": "Canceled",
        }
        await set_commit_status(job.commit.sha, status)

    async def stop_active_jobs_matching(self, job: MurdockJob) -> List[MurdockJob]:
        jobs_to_stop = []
        if job.pr is not None:
            jobs_to_stop += self.active.search_by_pr_number(job.pr.number)
        if job.ref is not None:
            jobs_to_stop += self.active.search_by_ref(job.ref)
        for job in jobs_to_stop:
            await self.stop_active_job(job)
        return jobs_to_stop

    async def stop_active_job_with_commit(self, commit: str):
        if (job := self.active.search_by_commit_sha(commit)) is None:
            return
        await self.stop_active_job(job)
        await self.reload_jobs()
        return job

    async def stop_active_job(self, job: MurdockJob) -> MurdockJob:
        LOGGER.debug(f"Stopping job {job}")
        await job.stop()
        status = {
            "state":"pending",
            "context": "Murdock",
            "target_url": GLOBAL_CONFIG.base_url,
            "description": "Stopped",
        }
        await set_commit_status(job.commit.sha, status)
        return job

    async def disable_jobs_matching(self, job: MurdockJob) -> List[MurdockJob]:
        LOGGER.debug(f"Disable jobs matching job {job}")
        disabled_jobs = []
        disabled_jobs += (await self.cancel_queued_jobs_matching(job))
        disabled_jobs += (await self.stop_active_jobs_matching(job))
        if disabled_jobs:
            await self.reload_jobs()
        return disabled_jobs

    async def restart_job(self, uid: str) -> MurdockJob:
        if (job := await self.db.find_job(uid)) is None:
            return
        LOGGER.info(f"Restarting job {job}")
        config = await fetch_murdock_config(job.commit.sha)
        new_job = MurdockJob(job.commit, pr=job.pr, ref=job.ref, config=config)
        await self.schedule_job(new_job)
        return new_job

    async def schedule_job(self, job: MurdockJob) -> MurdockJob:
        if self.sha_is_handled(job.commit.sha):
             LOGGER.debug(
                 f"Commit {job.commit.sha} is already handled, ignoring"
             )
             return
        LOGGER.info(f"Scheduling new job {job}")
        if GLOBAL_CONFIG.cancel_on_update is True:
            # Similar jobs are already queued or active => cancel/stop them
            await self.disable_jobs_matching(job)

        await self.add_job_to_queue(job)
        return job

    async def handle_pull_request_event(self, event: dict):
        if "action" not in event:
            return "Unsupported event"
        action = event["action"]
        if action not in ALLOWED_ACTIONS:
            return f"Unsupported action '{action}'"
        LOGGER.info(f"Handle pull request event '{action}'")
        pr_data = event["pull_request"]
        commit = await fetch_commit_info(pr_data["head"]["sha"])
        if commit is None:
            LOGGER.error("Cannot fetch commit information, aborting")
            return
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
            labels=sorted(
                [label["name"] for label in pr_data["labels"]]
            )
        )

        job = MurdockJob(commit, pr=pull_request, config=config)
        action = event["action"]
        if action == "closed":
            await self.disable_jobs_matching(job)
            return

        if any(
            line and re.match(rf"^({'|'.join(config.commit.skip_keywords)})$", line)
            for line in commit.message.split('\n')
        ):
            LOGGER.debug(
                f"Commit message contains skip keywords, skipping job {job}"
            )
            await set_commit_status(
                job.commit.sha,
                {
                    "state": "pending",
                    "context": "Murdock",
                    "description": "The build was skipped."
                }
            )
            return

        if action == "labeled":
            label = event["label"]["name"]
            if (
                CI_CONFIG.ready_label is not None and
                CI_CONFIG.ready_label not in pull_request.labels
            ):
                return
            elif (
                label == CI_CONFIG.ready_label and
                self.sha_is_handled(job.commit.sha)
            ):
                LOGGER.debug(
                    f"Commit {job.commit.sha} is already handled, ignoring"
                )
                return
            elif (
                label != CI_CONFIG.ready_label and
                (queued_job := self.queued.search_by_commit_sha(job.commit.sha)) is not None
            ):
                LOGGER.debug(
                    f"Updating queued job {queued_job} with new label '{label}'"
                )
                queued_job.pr.labels.append(label)
                return

        if CI_CONFIG.ready_label not in pull_request.labels:
            LOGGER.debug(f"'{CI_CONFIG.ready_label}' label not set")
            await self.disable_jobs_matching(job)
            status = {
                "state":"pending",
                "context": "Murdock",
                "target_url": GLOBAL_CONFIG.base_url,
                "description": f"\"{CI_CONFIG.ready_label}\" label not set",
            }
            await set_commit_status(job.commit.sha, status)
            return

        if (
            action == "unlabeled" and
            (queued_job := self.queued.search_by_commit_sha(job.commit.sha)) is not None
        ):
            label = event["label"]["name"]
            LOGGER.debug(
                f"Removing '{label}' from queued job {queued_job}"
            )
            queued_job.pr.labels.remove(label)

        await self.schedule_job(job)

    @staticmethod
    def handle_ref(ref: str, rules: List[str]) -> bool:
        return (
            "*" in rules or ref in rules or
            any(re.match(expr, ref) is not None for expr in rules)
        )

    async def handle_push_event(self, event: dict):
        ref_type, ref_name = event["ref"].split("/")[-2:]
        if event["after"] == "0000000000000000000000000000000000000000":
            LOGGER.debug(
                f"Ref was removed upstream, aborting all related jobs"
            )
            previous_ref = event["before"]
            await self.cancel_queued_job_with_commit(previous_ref)
            await self.stop_active_job_with_commit(previous_ref)
            return
        commit = await fetch_commit_info(event["after"])
        if commit is None:
            LOGGER.error("Cannot fetch commit information, aborting")
            return
        config = await fetch_murdock_config(commit.sha)
        if ((
            ref_type == "heads" and
            not Murdock.handle_ref(ref_name, config.push.branches)
        ) or (
            ref_type == "tags" and
            not Murdock.handle_ref(ref_name, config.push.tags)
        )):
            LOGGER.debug(f"Ref '{ref_name}' not accepted for push events")
            return

        job = MurdockJob(commit, ref=event["ref"], config=config)

        if any(
            re.match(rf"^({'|'.join(config.commit.skip_keywords)})$", line)
            for line in commit.message.split('\n')
        ):
            LOGGER.debug(
                f"Commit message contains skip keywords, skipping job {job}"
            )
            await set_commit_status(
                job.commit.sha,
                {
                    "state": "pending",
                    "context": "Murdock",
                    "description": "The build was skipped."
                }
            )
            return

        LOGGER.info(f"Handle push event on ref '{ref_name}'")
        await self.schedule_job(job)

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

    def get_queued_jobs(self) -> List[JobModel]:
        queued = sorted(
            [
                job.queued_model()
                for job in self.queued.jobs if job.canceled is False
            ],
            reverse=True, key=lambda job: job.since
        )
        return sorted(queued, key=lambda job: job.fasttracked)

    def get_active_jobs(self) -> List[JobModel]:
        return sorted(
            [
                job.running_model()
                for job in self.active.jobs if job is not None
            ], reverse=True, key=lambda job: job.since
        )

    async def remove_finished_jobs(self, query: JobQueryModel) -> List[FinishedJobModel]:
        jobs_before = await self.db.count_jobs(JobQueryModel(limit=-1))
        query.limit = -1
        jobs_count = await self.db.count_jobs(query)
        query.limit = jobs_count
        jobs_to_remove = await (self.db.find_jobs(query))
        for job in jobs_to_remove:
            work_dir = os.path.join(GLOBAL_CONFIG.work_dir, job.uid)
            MurdockJob.remove_dir(work_dir)
        await self.db.delete_jobs(query)
        jobs_removed = jobs_before - await self.db.count_jobs()
        LOGGER.info(f"{jobs_removed} jobs removed")
        await self.reload_jobs()
        return jobs_to_remove


    async def get_jobs(self, limit: int) -> CategorizedJobsModel:
        return CategorizedJobsModel(
            queued=self.get_queued_jobs(),
            building=self.get_active_jobs(),
            finished=await self.db.find_jobs(limit)
        )

    async def handle_commit_status_data(
        self, commit: str, data: dict
    ) -> MurdockJob:
        job = self.active.search_by_commit_sha(commit)
        if job is not None and "status" in data and data["status"]:
            job.status = data["status"]
            data.update({"cmd": "status", "commit": commit})
            await self._broadcast_message(json.dumps(data))
        return job
