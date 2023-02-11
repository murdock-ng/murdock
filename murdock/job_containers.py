from abc import ABC, abstractmethod, abstractproperty
from typing import List, Optional, Sequence

from murdock.job import MurdockJob
from murdock.models import JobQueryModel


class MurdockJobListBase(ABC):
    _jobs: List[Optional[MurdockJob]]

    @abstractproperty
    def jobs(self) -> Sequence[Optional[MurdockJob]]:
        ...  # pragma: nocover

    @abstractmethod
    def add(self, *jobs: MurdockJob) -> None:
        ...  # pragma: nocover

    @abstractmethod
    def remove(self, job: MurdockJob) -> None:
        ...  # pragma: nocover

    def len(self) -> int:
        # Get the length of the generator without having to cast it to a list/tuple
        return sum(1 for job in self.jobs if job is not None)

    def search_by_uid(self, uid: str) -> Optional[MurdockJob]:
        for job in self._jobs:
            if job is not None and job.uid == uid:
                return job
        return None

    def search_by_pr_number(self, prnum: int) -> List[MurdockJob]:
        return self.search_with_query(JobQueryModel(prnum=prnum))

    def search_by_ref(self, ref: str) -> List[MurdockJob]:
        return self.search_with_query(JobQueryModel(ref=ref))

    def search_matching(self, job: MurdockJob) -> List[MurdockJob]:
        result = []
        if job.pr is not None:
            result += self.search_by_pr_number(job.pr.number)
        if job.ref is not None:
            result += self.search_by_ref(job.ref)
        return result

    def search_with_query(self, query: JobQueryModel) -> List[MurdockJob]:
        jobs = {job for job in self.jobs if job is not None}
        uid_job = jobs
        is_pr_jobs = jobs
        is_branch_jobs = jobs
        is_tag_jobs = jobs
        prnum_jobs = jobs
        prstates_jobs = jobs
        branch_jobs = jobs
        tag_jobs = jobs
        ref_jobs = jobs
        sha_jobs = jobs
        author_jobs = jobs

        if not jobs:
            return []

        if query.uid is not None:
            if (job_found := self.search_by_uid(query.uid)) is not None:
                uid_job = set([job_found])
            else:
                uid_job = set()
        if query.is_pr is not None:
            if query.is_pr is True:
                is_pr_jobs = {
                    job for job in self.jobs if job is not None and job.pr is not None
                }
            else:
                is_pr_jobs = {
                    job for job in self.jobs if job is not None and job.pr is None
                }
        if query.is_branch is not None:
            if query.is_branch is True:
                is_branch_jobs = {
                    job
                    for job in self.jobs
                    if (
                        job is not None
                        and job.ref is not None
                        and job.ref.startswith("refs/heads/")
                    )
                }
            else:
                is_branch_jobs = {
                    job
                    for job in self.jobs
                    if (
                        job is not None
                        and (
                            job.ref is None
                            or (
                                job.ref is not None
                                and not job.ref.startswith("refs/heads/")
                            )
                        )
                    )
                }
        if query.is_tag is not None:
            if query.is_tag is True:
                is_tag_jobs = {
                    job
                    for job in self.jobs
                    if (
                        job is not None
                        and job.ref is not None
                        and job.ref.startswith("refs/tags/")
                    )
                }
            else:
                is_tag_jobs = {
                    job
                    for job in self.jobs
                    if (
                        job is not None
                        and (
                            job.ref is None
                            or (
                                job.ref is not None
                                and not job.ref.startswith("refs/tags/")
                            )
                        )
                    )
                }
        if query.prnum is not None:
            prnum_jobs = {
                job
                for job in self.jobs
                if (
                    job is not None
                    and job.pr is not None
                    and job.pr.number == query.prnum
                )
            }
        if query.prstates is not None:
            prstates_jobs = {
                job
                for job in self.jobs
                if (
                    job is not None
                    and job.pr is not None
                    and job.pr.state in query.prstates.split(" ")
                )
            }
        if query.branch is not None:
            branch_jobs = {
                job
                for job in self.jobs
                if job is not None
                and job.ref is not None
                and job.ref == f"refs/heads/{query.branch}"
            }
        if query.tag is not None:
            tag_jobs = {
                job
                for job in self.jobs
                if job is not None
                and job.ref is not None
                and job.ref == f"refs/tags/{query.tag}"
            }
        if query.ref is not None:
            ref_jobs = {
                job
                for job in self.jobs
                if job is not None and job.ref is not None and job.ref == query.ref
            }
        if query.sha is not None:
            sha_jobs = {
                job
                for job in self.jobs
                if job is not None and job.commit.sha == query.sha
            }
        if query.author is not None:
            author_jobs = {
                job
                for job in self.jobs
                if job is not None and job.commit.author == query.author
            }
        return sorted(
            list(
                jobs.intersection(uid_job)
                .intersection(is_pr_jobs)
                .intersection(is_branch_jobs)
                .intersection(is_tag_jobs)
                .intersection(prnum_jobs)
                .intersection(prstates_jobs)
                .intersection(branch_jobs)
                .intersection(tag_jobs)
                .intersection(ref_jobs)
                .intersection(sha_jobs)
                .intersection(author_jobs)
            ),
            reverse=True,
            key=lambda job: job.creation_time.timestamp(),
        )


class MurdockJobList(MurdockJobListBase):
    def __init__(self):
        self._jobs = []

    @property
    def jobs(self) -> Sequence[Optional[MurdockJob]]:
        return self._jobs

    def add(self, *jobs: MurdockJob) -> None:
        self._jobs += jobs

    def remove(self, job: MurdockJob) -> None:
        if job in self._jobs:
            self._jobs.remove(job)


class MurdockJobPool(MurdockJobListBase):
    def __init__(self, maxlen: int):
        self._jobs = maxlen * [None]  # type: ignore[assignment]

    @property
    def jobs(self) -> Sequence[Optional[MurdockJob]]:
        return self._jobs

    def add(self, *jobs: MurdockJob) -> None:
        for job in jobs:
            for index, current in enumerate(self.jobs):
                if current is None:
                    self._jobs[index] = job
                    break

    def remove(self, job: MurdockJob) -> None:
        for index, current in enumerate(self.jobs):
            if current == job:
                self._jobs[index] = None
