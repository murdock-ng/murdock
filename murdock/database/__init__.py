import abc
from typing import List, Optional, Any

from murdock.job import MurdockJob
from murdock.models import JobModel, JobQueryModel
from murdock.config import DB_CONFIG


class Database(abc.ABC):
    @abc.abstractmethod
    def __init__(self):
        """Constructor for the database backend."""

    @abc.abstractmethod
    async def init(self):
        """Initialize the database backend."""

    @abc.abstractmethod
    def close(self):
        """Close all database connections."""

    @abc.abstractmethod
    async def insert_job(self, job: MurdockJob):
        """Insert a job into the database.

        All properties of the job are serialized into a database-specific format and inserted as new record or document
        into the database.

        :param job: The job to insert.
        """

    @abc.abstractmethod
    async def find_job(self, uid: str) -> Optional[MurdockJob]:
        """Find a single job based on the job UID.

        :param uid: The UID to find the job for.
        :return: The job with the matching UID, None if no job is found.
        """

    @abc.abstractmethod
    async def find_jobs(self, query: JobQueryModel) -> List[JobModel]:
        """Find all jobs that match the given query.

        :param query: JobQueryModel to match jobs on.
        :returns: A list containing matching jobs.
        """

    @abc.abstractmethod
    async def update_jobs(self, query: JobQueryModel, field: str, value: Any) -> int:
        """Update a single field of the matching jobs.

        :param query: JobQueryModel to match jobs on.
        :param field: Field descriptor to update.
        :param value: Value to set the field on.
        :return: The number of updated records.
        """

    @abc.abstractmethod
    async def count_jobs(self, query: JobQueryModel) -> int:
        """Count the number of jobs matching the query.

        :param query: JobQueryModel to match jobs on.
        :return: The number of matching records.
        """

    @abc.abstractmethod
    async def delete_jobs(self, query: JobQueryModel):
        """Delete all matching jobs.

        :param query: JobQueryModel to match jobs on.
        """


def database(database_type: str) -> Database:
    if database_type == "mongodb":
        from murdock.database import mongodb

        return mongodb.MongoDatabase()
    raise ValueError(f"Invalid database type specified: {database_type!r}")


def database_from_env() -> Database:
    return database(DB_CONFIG.type)
