#!/usr/bin/env python
from enum import Enum
from threading import Lock
import time

from .log import log

class JobState(Enum):
    created = 0
    queued = 1
    running = 2
    finished = 3

class JobResult(Enum):
    passed = 0
    canceled = 1
    errored = 2
    timeout = 3
    unknown = 4

class Job(object):
    id = 0
    def __init__(s, name, cmd, env=None, hook=None, arg=None):
        s.lock = Lock()
        s.id = Job.id
        Job.id += 1

        s.name = name
        s.cmd = cmd
        s.env = env
        s.worker = None

        s.hook = hook
        s.arg = arg

        s.result = JobResult.unknown

        s.time_created = -1
        s.time_queued = -1
        s.time_started = -1
        s.time_finished = -1

        s.set_state(JobState.created)

    def data_dir(s):
        return s.name #,os.path.join(config.data_dir, s.name + "." + str(s.id))

    def set_state(s, state, result=JobResult.unknown):
        with s.lock:
            s.state = state
            s.result = result
            if s.state == JobState.created:
                s.time_created = time.time()
            elif s.state == JobState.queued:
                s.time_queued = time.time()
            elif s.state == JobState.running:
                s.time_started = time.time()
            elif s.state == JobState.finished:
                s.time_finished = time.time()

            log.info("Job %s: new state: %s %s %s %s %s %s " , s.name, s.state, s.result, s.time_created, s.time_queued, s.time_started, s.time_finished)

        if s.hook:
            s.hook(s.arg, s)

    def stopped(s, result):
        with s.lock:
            s.state = result

    def cancel(s):
        if s.worker:
            s.worker.cancel(s)
        else:
            s.set_state(JobState.finished, JobResult.canceled)
