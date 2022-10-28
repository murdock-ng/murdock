import asyncio

from typing import List, Optional, Any

import pymongo
import motor.motor_asyncio as aiomotor

from murdock.config import DB_CONFIG
from murdock.log import LOGGER
from murdock.job import MurdockJob
from murdock.models import CommitModel, JobModel, JobQueryModel, PullRequestInfo
from murdock.database import Database


class MongoDatabase(Database):
    def __init__(self):
        LOGGER.info("Initializing database connection")
        conn = aiomotor.AsyncIOMotorClient(
            f"mongodb://{DB_CONFIG.host}:{DB_CONFIG.port}",
            maxPoolSize=5,
            io_loop=asyncio.get_event_loop(),
        )
        conn.get_io_loop = asyncio.get_event_loop
        self.db = conn[DB_CONFIG.name]

    async def init(self):
        await self.db.job.create_index([("uid", 1)], unique=True, name="job_uid")
        await self.db.job.create_index(
            [("creation_time", pymongo.ASCENDING)], name="job_creation_time"
        )

    async def close(self):
        LOGGER.info("Closing database connection")
        self.db.client.close()

    async def insert_job(self, job: MurdockJob):
        LOGGER.debug(f"Inserting {job} to database")
        await self.db.job.insert_one(MurdockJob.to_db_entry(job))

    async def find_job(self, uid: str) -> Optional[MurdockJob]:
        if not (entry := await self.db.job.find_one({"uid": uid})):
            LOGGER.warning(f"Cannot find job matching uid '{uid}'")
            return None

        commit = CommitModel(**entry["commit"])
        prinfo = PullRequestInfo(**entry["prinfo"]) if "prinfo" in entry else None
        ref = entry["ref"] if "ref" in entry else None
        user_env = entry["user_env"] if "user_env" in entry else None

        return MurdockJob(commit, pr=prinfo, ref=ref, user_env=user_env)

    async def find_jobs(self, query: JobQueryModel) -> List[JobModel]:
        jobs = await (
            self.db.job.find(query.to_mongodb_query())
            .sort("creation_time", -1)
            .to_list(length=query.limit)
        )
        return [MurdockJob.finished_model(job) for job in jobs]

    async def update_jobs(self, query: JobQueryModel, field: str, value: Any) -> int:
        return (
            await self.db.job.update_many(
                query.to_mongodb_query(), {"$set": {field: value}}
            )
        ).modified_count

    async def count_jobs(self, query: JobQueryModel) -> int:
        return await self.db.job.count_documents(query.to_mongodb_query())

    async def delete_jobs(self, query: JobQueryModel):
        await self.db.job.delete_many(query.to_mongodb_query())
