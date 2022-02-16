import asyncio

from typing import List

import pymongo
import motor.motor_asyncio as aiomotor

from murdock.config import DB_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel, JobModel, JobQueryModel, PullRequestInfo


class Database:

    db = None

    async def init(self):
        LOGGER.info("Initializing database connection")
        conn = aiomotor.AsyncIOMotorClient(
            f"mongodb://{DB_CONFIG.host}:{DB_CONFIG.port}",
            maxPoolSize=5,
            io_loop=asyncio.get_event_loop(),
        )
        self.db = conn[DB_CONFIG.name]
        await self.db.job.create_index([("uid", 1)], unique=True, name="job_uid")
        await self.db.job.create_index(
            [("creation_time", pymongo.ASCENDING)], name="job_creation_time"
        )

    def close(self):
        LOGGER.info("Closing database connection")
        self.db.client.close()

    async def insert_job(self, job: MurdockJob):
        LOGGER.debug(f"Inserting job {job} to database")
        await self.db.job.insert_one(MurdockJob.to_db_entry(job))

    async def find_job(self, uid: str) -> MurdockJob:
        if not (entry := await self.db.job.find_one({"uid": uid})):
            LOGGER.warning(f"Cannot find job matching uid '{uid}'")
            return

        commit = CommitModel(**entry["commit"])
        if "prinfo" in entry:
            prinfo = PullRequestInfo(**entry["prinfo"])
        else:
            prinfo = None
        if "ref" in entry:
            ref = entry["ref"]
        else:
            ref = None
        if "user_env" in entry:
            user_env = entry["user_env"]
        else:
            user_env = None

        return MurdockJob(commit, pr=prinfo, ref=ref, user_env=user_env)

    async def find_jobs(self, query: JobQueryModel) -> List[JobModel]:
        jobs = await (
            self.db.job.find(query.to_mongodb_query())
            .sort("creation_time", -1)
            .to_list(length=query.limit)
        )
        return [MurdockJob.finished_model(job) for job in jobs]

    async def update_jobs(self, query: JobQueryModel, field, value) -> int:
        return (
            await self.db.job.update_many(
                query.to_mongodb_query(), {"$set": {field: value}}
            )
        ).modified_count

    async def count_jobs(self, query: JobQueryModel) -> int:
        return await self.db.job.count_documents(query.to_mongodb_query())

    async def delete_jobs(self, query: JobQueryModel):
        await self.db.job.delete_many(query.to_mongodb_query())
