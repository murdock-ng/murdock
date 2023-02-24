import asyncio
import os
import shlex
import signal

from typing import Optional, Callable
from asyncio.subprocess import Process

import structlog

from murdock.config import GLOBAL_CONFIG, TaskSettings
from murdock.log import LOGGER


class Task:
    def __init__(
        self,
        index: int,
        config: TaskSettings,
        job_uid: str,
        job_env: dict,
        extend_job_output: Callable,
        scripts_dir: str,
        work_dir: str,
        run_in_docker: bool = GLOBAL_CONFIG.run_in_docker,
        logger: Optional[structlog.BoundLogger] = None,
    ):
        self.index = index
        self.config = config
        self.job_uid = job_uid
        self.scripts_dir = scripts_dir
        self.work_dir = work_dir
        self.run_in_docker = run_in_docker
        task_env = job_env.copy()
        task_env.update(config.env)
        self.env = task_env
        self.extend_job_output = extend_job_output
        self.proc: Optional[Process] = None
        self.stopped: bool = False

        logger = logger or LOGGER
        self._logger = logger.bind(
            task=self.index, name=self.config.name or "", dir=self.work_dir
        )

    def __repr__(self):
        return (
            f"Task {self.index}"
            if self.config.name is None
            else f"'{self.config.name}' task"
        )

    def _docker_cmd_args(self):
        env_to_docker = " ".join(
            [f'--env "{key}={value}"' for key, value in self.env.items()]
        )
        volumes = GLOBAL_CONFIG.docker_volumes.copy()
        host_job_work_dir = os.path.join(GLOBAL_CONFIG.host_work_dir, self.job_uid)
        volumes.update({host_job_work_dir: "/murdock"})
        volumes_to_docker = " ".join(
            [f"--volume {key}:'{value}'" for key, value in volumes.items()]
        )
        docker_image = (
            GLOBAL_CONFIG.docker_default_image
            if self.config.image is None
            else self.config.image
        )
        docker_cmd = "" if self.config.command is None else self.config.command
        command = "/usr/bin/docker"
        args = shlex.split(
            "run --rm --stop-signal SIGINT "
            f"--cpus={GLOBAL_CONFIG.docker_cpu_limit} "
            f"--memory={GLOBAL_CONFIG.docker_mem_limit} "
            f"--network container:murdock-api-{GLOBAL_CONFIG.project} "
            f"--user {GLOBAL_CONFIG.docker_user_uid}:{GLOBAL_CONFIG.docker_user_gid} "
            f"{env_to_docker} {volumes_to_docker} "
            f"--name murdock-job-{self.job_uid} "
            "--workdir /murdock "
            f"{docker_image} {docker_cmd}"
        )
        return command, args

    async def exec(self):
        if self.run_in_docker is True:
            command, args = self._docker_cmd_args()
        else:
            command = os.path.join(self.scripts_dir, GLOBAL_CONFIG.script_name)
            args = []
        self._logger.debug("Launching", command=command, command_args=" ".join(args))
        self.proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=self.work_dir if self.run_in_docker is False else None,
            env=self.env if self.run_in_docker is False else None,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        state = "running"
        while True:
            data = await self.proc.stdout.readline()  # type: ignore[union-attr]
            if not data:
                break
            line = data.decode()
            await self.extend_job_output(line.replace("\r", "\n"))
        await self.proc.wait()

        if self.proc.returncode == 0:
            state = "passed"
        elif self.stopped is True:
            state = "stopped"
        else:
            state = "errored"

        self._logger.debug(
            "Task executed", state=state, return_code=self.proc.returncode
        )

        self.proc = None

        return state

    async def stop(self) -> None:
        self._logger.info("Immediate stop requested")
        if self.proc is not None:
            self.stopped = True
        if self.run_in_docker:
            await asyncio.create_subprocess_exec(
                "/usr/bin/docker",
                *shlex.split(f"stop --time 5 murdock-job-{self.job_uid}"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            if self.proc is not None and self.proc.returncode is None:
                self._logger.debug("Send signal SIGINT to task")
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._logger.warning("Couldn't stop task with SIGINT")
