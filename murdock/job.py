import asyncio
import os
import shutil
import signal
import time

from murdock.config import (
    GITHUB_REPO, MURDOCK_BASE_URL, MURDOCK_ROOT_DIR, MURDOCK_SCRIPTS_DIR,
    MURDOCK_USE_SECURE_API, MURDOCK_API_SECRET
)
from murdock.log import LOGGER


class MurdockJob:

    def __init__(self, pr, fasttracked=False):
        self.result = -1
        self.proc = None
        self.output = ""
        self.pr = pr
        self.start_time = time.time()
        self.stop_time = 0
        self.canceled = False
        self.status = ""
        self.fasttracked = fasttracked
        work_dir_relative = os.path.join(
            GITHUB_REPO,
            self.pr.number,
            self.pr.commit
        )
        self.work_dir = os.path.join(MURDOCK_ROOT_DIR, work_dir_relative)
        MurdockJob.create_dir(self.work_dir)
        self.http_dir = os.path.join(MURDOCK_BASE_URL, work_dir_relative)
        self.output_url = os.path.join(self.http_dir, "output.html")

    @staticmethod
    def create_dir(work_dir):
        try:
            LOGGER.debug(f"Creating directory '{work_dir}'")
            os.makedirs(work_dir)
        except FileExistsError:
            LOGGER.debug(f"Directory '{work_dir}' already exists, recreate")
            shutil.rmtree(work_dir)
            os.makedirs(work_dir)

    @staticmethod
    def remove_dir(work_dir):
        try:
            shutil.rmtree(work_dir)
        except FileNotFoundError:
            LOGGER.debug(
                f"Directory '{work_dir}' doesn't exist, cannot remove"
            )

    @property
    def runtime(self):
        return self.stop_time - self.start_time

    @property
    def runtime_human(self):
        if self.runtime > 86400:
            runtime_format = "%Dd:%Hh:%Mm:%Ss"
        elif self.runtime > 3600:
            runtime_format = "%Hh:%Mm:%Ss"
        elif self.runtime > 60:
            runtime_format = "%Mm:%Ss"
        else:
            runtime_format = "%Ss"
        return time.strftime(runtime_format, time.gmtime(self.runtime))

    @staticmethod
    def to_db_entry(job):
        return {
            "title" : job.pr.title,
            "user" : job.pr.user,
            "url" : job.pr.url,
            "commit" : job.pr.commit,
            "prnum": job.pr.number,
            "since" : job.start_time,
            "runtime": job.runtime,
            "result": job.result,
            "output_url": job.output_url,
            "status": job.status,
        }

    @staticmethod
    def from_db_entry(entry):
        return {
            "title" : entry["title"],
            "user" : entry["user"],
            "url" : entry["url"],
            "commit" : entry["commit"],
            "prnum": entry["prnum"],
            "since" : entry["since"],
            "result" : entry["result"],
            "output_url": entry["output_url"],
            "runtime" : entry["runtime"],
            "status": entry["status"],
        }

    @property
    def env(self):
        _env = { 
            "CI_PULL_COMMIT" : self.pr.commit,
            "CI_PULL_REPO" : GITHUB_REPO,
            "CI_PULL_BRANCH" : self.pr.branch,
            "CI_PULL_NR" : self.pr.number,
            "CI_PULL_URL" : self.pr.url,
            "CI_PULL_TITLE" : self.pr.title,
            "CI_PULL_USER" : self.pr.user,
            "CI_BASE_REPO" : self.pr.base_repo,
            "CI_BASE_BRANCH" : self.pr.base_branch,
            "CI_BASE_COMMIT" : self.pr.base_commit,
            "CI_SCRIPTS_DIR" : MURDOCK_SCRIPTS_DIR,
            "CI_PULL_LABELS" : ";".join(self.pr.labels),
            "CI_BUILD_HTTP_ROOT" : self.http_dir,
            "CI_BASE_URL": MURDOCK_BASE_URL,
        }

        if MURDOCK_USE_SECURE_API:
            _env.update({
                "CI_API_SECRET": MURDOCK_API_SECRET,
            })

        if self.pr.mergeable:
            _env.update({"CI_MERGE_COMMIT": self.pr.merge_commit})

        return _env

    def __repr__(self):
        return (
            f"sha:{self.pr.commit[0:7]} (PR #{self.pr.number}))"
        )

    def __eq__(self, other):
        return other is not None and self.pr.commit == other.pr.commit

    async def execute(self):
        LOGGER.debug(f"Launching build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(MURDOCK_SCRIPTS_DIR, "build.sh"), "build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, _ = await self.proc.communicate()
        self.output = out.decode()
        if self.proc.returncode == 0:
            self.result = "passed"
        elif self.proc.returncode in [
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            self.result = "stopped"
        else:
            self.result = "errored"
        LOGGER.debug(
            f"Job {self} {self.result} (ret: {self.proc.returncode})"
        )

        # If the job was stopeed, just return now and skip the post_build action
        if self.result == "stopped":
            LOGGER.debug(f"Job {self} stopped before post_build action")
            self.proc = None
            return

        # Store build output in text file
        try:
            with open(os.path.join(
                self.work_dir, "output.txt"), "w"
            ) as output:
                output.write(self.output)
        except Exception as exc:
            LOGGER.warning(
                f"Job error for {self}: cannot write output.txt: {exc}"
            )

        # Remove build subdirectory
        MurdockJob.remove_dir(os.path.join(self.work_dir, "build"))

        LOGGER.debug(f"Launch post_build action for {self}")
        self.proc = await asyncio.create_subprocess_exec(
            os.path.join(MURDOCK_SCRIPTS_DIR, "build.sh"), "post_build",
            cwd=self.work_dir,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, err = await self.proc.communicate()
        if self.proc.returncode not in [
            0,
            int(signal.SIGINT) * -1,
            int(signal.SIGKILL) * -1,
            int(signal.SIGTERM) * -1,
        ]:
            LOGGER.warning(
                f"Job error for {self}: Post build action failed:\n"
                f"out: {out.decode()}"
                f"err: {err.decode()}"
            )
        self.proc = None

    async def stop(self):
        LOGGER.debug(f"Job {self} immediate stop requested")
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGKILL]:
            if self.proc.returncode is None:
                LOGGER.debug(f"Send {sig} to job {self}")
                self.proc.send_signal(sig)
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    LOGGER.debug(f"Couldn't stop job {self} with {sig}")
        LOGGER.debug(f"Removing job working directory '{self.work_dir}'")
        MurdockJob.remove_dir(self.work_dir)
