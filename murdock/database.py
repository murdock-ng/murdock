import asyncio

from datetime import datetime
from datetime import time as dtime
from typing import Optional

import motor.motor_asyncio as aiomotor

from murdock.config import CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel, PullRequestInfo


class Database:

    db = None

    async def init(self):
        LOGGER.info("Initializing database connection")
        loop = asyncio.get_event_loop()
        conn = aiomotor.AsyncIOMotorClient(
            f"mongodb://{CONFIG.murdock_db_host}:{CONFIG.murdock_db_port}",
            maxPoolSize=5,
            io_loop=loop
        )
        self.db = conn[CONFIG.murdock_db_name]
    
    def close(self):
        LOGGER.info("Closing database connection")
        self.db.client.close()

    async def insert_job(self, job : MurdockJob):
        LOGGER.debug(f"Inserting job {job} to database")
        await self.db.job.insert_one(MurdockJob.to_db_entry(job))

    async def find_job(self, uid : str) -> MurdockJob:
        if not (entry := await self.db.job.find_one({"uid": uid})):
            LOGGER.warning(f"Cannot find job matching uid '{uid}'")
            return

        commit = CommitModel(**entry["commit"])
        if entry["prinfo"] is not None:
            prinfo = PullRequestInfo(**entry["prinfo"])
        else:
            prinfo = None
        
        return MurdockJob(commit, pr=prinfo, branch=entry["branch"])

    @staticmethod
    def query(
        uid: Optional[str] = None,
        prnum: Optional[int] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
        author: Optional[str] = None,
        result: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None
    ):
        _query = {}
        if uid is not None:
            _query.update({"uid": uid})
        if prnum is not None:
            _query.update({"prinfo.number": prnum})
        if branch is not None:
            _query.update({"branch": branch})
        if sha is not None:
            _query.update({"commit.sha": sha})
        if author is not None:
            _query.update({"commit.author": author})
        if result in ["errored", "passed"]:
            _query.update({"result": result})
        if after is not None:
            date = datetime.strptime(after, "%Y-%m-%d")
            _query.update({"since": {"$gte": date.timestamp()}})
        if before is not None:
            date = datetime.combine(
                datetime.strptime(before, "%Y-%m-%d"),
                dtime(hour=23, minute=59, second=59, microsecond=999)
            )
            if "since" in _query:
                _query["since"].update({"$lte": date.timestamp()})
            else:
                _query.update({"since": {"$lte": date.timestamp()}})
        return _query

    async def find_jobs(
        self,
        limit: int,
        uid: Optional[str] = None,
        prnum: Optional[int] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
        author: Optional[str] = None,
        result: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None
    ) -> list:
        query = Database.query(
            uid, prnum, branch, sha, author, result, after, before
        )
        jobs = await (
            self.db.job.find(query).sort("since", -1).to_list(length=limit)
        )

        return [MurdockJob.from_db_entry(job) for job in jobs]

    async def count_jobs(
        self,
        uid: Optional[str] = None,
        prnum: Optional[int] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
        author: Optional[str] = None,
        result: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None
    ) -> list:
        query = Database.query(
            uid, prnum, branch, sha, author, result, after, before
        )
        return await self.db.job.count_documents(query)

    async def delete_jobs(
        self,
        uid: Optional[str] = None,
        prnum: Optional[int] = None,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
        author: Optional[str] = None,
        result: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None
    ):
        query = Database.query(
            uid, prnum, branch, sha, author, result, after, before
        )
        await self.db.job.delete_many(query)
