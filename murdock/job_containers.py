from abc import ABC, abstractclassmethod
from typing import List, Deque
from collections import deque

from murdock.job import MurdockJob


class MurdockJobListBase(ABC):

    _jobs : List[MurdockJob]

    @property
    @abstractclassmethod
    def jobs(self) -> List[MurdockJob]:
        ...  # pragma: nocover

    @abstractclassmethod
    def add(self, *jobs: MurdockJob):
        ...  # pragma: nocover

    @abstractclassmethod
    def remove(self, job: MurdockJob):
        ...  # pragma: nocover

    def search_by_uid(self, uid : str) -> MurdockJob:
        for job in self._jobs:
            if job is not None and job.uid == uid:
                return job

    def search_by_commit_sha(self, sha) -> MurdockJob:
        for job in self._jobs:
            if job is not None and job.commit.sha == sha:
                return job

    def search_by_pr_number(self, prnum : int) -> List[MurdockJob]:
        return [
            job for job in self._jobs
            if (
                job is not None and
                job.pr is not None and
                job.pr.number == prnum
            )
        ]

    def search_by_ref(self, ref : str) -> List[MurdockJob]:
        return [
            job for job in self._jobs
            if (
                job is not None and
                job.ref is not None and
                job.ref == ref
            )
        ]  


class MurdockJobList(MurdockJobListBase):

    def __init__(self):
        self._jobs = []

    @property
    def jobs(self) -> List[MurdockJob]:
        return self._jobs

    def add(self, *jobs: MurdockJob):
        self._jobs += jobs

    def remove(self, job: MurdockJob):
        self._jobs.remove(job)


class MurdockJobPool(MurdockJobListBase):

    def __init__(self, maxlen : int):
        self._jobs = maxlen * [None]

    @property
    def jobs(self) -> List[MurdockJob]:
        return self._jobs

    def add(self, *jobs: MurdockJob):
        for job in jobs:
            for index, current in enumerate(self.jobs):
                if current is None:
                    self._jobs[index] = job
                    break

    def remove(self, job: MurdockJob):
        for index, current in enumerate(self.jobs):
            if current == job:
                self._jobs[index] = None
