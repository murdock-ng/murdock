#!/usr/bin/env python

import os
import pickle
import subprocess
import sys
import json
import time
import signal
import shutil
import tornado.ioloop

from log import log

from agithub.GitHub import GitHub

from os.path import abspath
import threading
from threading import Thread
from queue import Queue, Empty

from enum import Enum

from jobs import *
from github_webhook import *

import config

class ShellWorker(threading.Thread):
    def __init__(self, queue):
        threading.Thread.__init__(self, daemon=True)
        self.process = None
        self.queue = queue
        self.canceled = False
        self.job = None
        self.start()

    def run(s):
        log.info("ShellWorker: started.")
        while True:
            try:
                s.job = None
                s.process = None
                s.canceled = False
                job = s.queue.get()
                s.job = job
            except Empty:
                return
            if job.state == JobState.finished:
                log.info("ShellWorker: skipping finished job %s", job.name)
                s.queue.task_done()
                continue
            else:
                log.info("ShellWorker: building job %s", job.name)

            s.job = job
            job.worker = s
            s.job.set_state(JobState.running)

            build_dir = os.path.join(s.job.data_dir(), "build")
            try:
                os.makedirs(build_dir)
            except FileExistsError:
                shutil.rmtree(build_dir)
                os.makedirs(build_dir)

            output_file = open(os.path.join(s.job.data_dir(), "output.txt"), mode='wb')
            s.process = p = subprocess.Popen(s.job.cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,
                         cwd=s.job.data_dir(), env=s.job.env)
            s.job.worker = s
            try:
                for line in p.stdout:
                    output_file.write(line)
            except Exception as e:
                log.info(e)
            finally:
                output_file.close()

            log.info("ShellWorker: Job %s finished. result: %s", s.job.name, s.job.result)
            s.process.wait()

            if s.canceled:
                s.job.set_state(JobState.finished, JobResult.canceled)
            else:
                ret = s.process.returncode
                s.process = None
                if ret == 0:
                    s.job.set_state(JobState.finished, JobResult.passed)
                else:
                    s.job.set_state(JobState.finished, JobResult.errored)

            try:
                shutil.rmtree(build_dir)
            except FileNotFoundError:
                pass

            s.queue.task_done()

    def cancel(s, job):
        if s.process is not None and s.job==job:
            s.process.terminate()
            s.process.wait()
            s.canceled=True

class PullRequest(object):
    _map = {}

    def __init__(s, data):
        s.data = data
        s._map[data["_links"]["html"]["href"]] = s
        s.current_job = None
        s.jobs = []
        s.labels = None
        s.old_head = None

    def get(data):
        if "pull_request" in data:
            data = data["pull_request"]

        pull_url = data["_links"]["html"]["href"]
        pr = PullRequest._map.get(pull_url)
        if pr:
            pr.data = data
            log.info("PR %s updated", pr.url)
        else:
            pr = PullRequest(data)
            pr.update_labels()
            log.info("PR %s added", pr.url)
        return pr

    def update(s):
        if s.head != s.old_head:
            s.old_head = s.head
            log.info("PR %s has new head: %s", s.url, s.head)
            if "Ready for CI build" in s.labels:
                s.start_job()
        return s

    def cancel_job(s):
        if s.current_job:
            log.info("PR %s: canceling build of commit %s", s.url, s.current_job.arg)
            s.current_job.cancel()
            s.current_job = None
        return s

    def start_job(s):
        s.cancel_job()

        env = { "CI_PULL_COMMIT" : s.head,
                "CI_PULL_REPO" : s.repo,
                "CI_PULL_BRANCH" : s.branch,
                "CI_PULL_NR" : str(s.nr),
                "CI_PULL_URL" : s.url,
                "CI_BASE_REPO" : s.base_repo,
                "CI_BASE_BRANCH" : s.base_branch,
                "CI_BASE_COMMIT" : s.base_commit,
                "CI_SCRIPTS_DIR" : scripts_dir
                }

        s.current_job = Job(s.get_job_path(s.head), os.path.abspath("./build.sh"), env, s.job_hook, s.head)
        s.jobs.append(s.current_job)
        queue.put(s.current_job)

        s.current_job.set_state(JobState.queued)
        log.info("PR %s: queueing build of commit %s", s.url, s.head)
        return s

    def get_job_path(s, commit):
        return os.path.join(config.data_dir, s.base_full_name, str(s.nr), commit)

    def update_labels(s):
        code, result = github.repos[s.base_full_name].issues[s.nr].labels.get()
        if code == 200:
            s.labels = set()
            for label in result:
                s.labels.add(label["name"])
        return s

    def add_label(s, label):
        s.labels.add(label)
        if label == "Ready for CI build":
            s.start_job()
        return s

    def remove_label(s, label):
        s.labels.discard(label)
        if label == "Ready for CI build":
            s.cancel_job()
        return s

    def __getattr__(s, field):
        if field == "url":
            return s.data["_links"]["html"]["href"]
        elif field == "base_repo":
            return s.data["base"]["repo"]["clone_url"]
        elif field == "base_branch":
            return s.data["base"]["ref"]
        elif field == "base_commit":
            return s.data["base"]["sha"]
        elif field == "base_full_name":
            return s.data["base"]["repo"]["full_name"]
        elif field == "nr":
            return s.data["number"]
        elif field == "branch":
            return s.data["head"]["ref"]
        elif field == "repo":
            return s.data["head"]["repo"]["clone_url"]
        elif field == "head":
            return s.data["head"]["sha"]
        else:
            raise AttributeError

    def job_hook(s, arg, job):
        context = "RIOT CI"
        target_url = None

        if job.state == JobState.created:
            state = "pending"
            description = "The build has been queued."
        elif job.state == JobState.running:
            state = "pending"
            description = "The build has been started."
        elif job.state == JobState.finished:
            target_url = os.path.join(config.http_root, s.base_full_name, str(s.nr), arg, "output.txt")
            if job.result == JobResult.passed:
                state = "success"
                description = "The build succeeded."
            elif job.result == JobResult.errored:
                state = "error"
                description = "The build failed."
            else:
                state = "failure"
                if job.result == JobResult.canceled:
                    description = "The build has been canceled."
                else:
                    description = "The build has failed for an unknown reason."
        else:
            return

        status = {
                "state": state,
                "description": description,
                "context": context
                }
        if target_url:
            status["target_url"] = target_url

        github.repos[s.base_full_name].statuses[arg].post(body=json.dumps(status))

    def cancel_all():
        log.info("canceling jobs...")
        for url, pr in PullRequest._map.items():
            if pr.current_job:
                pr.current_job.cancel()

    def load(repo):
        code, result = github.repos[repo].pulls.get()
        if code==200:
            for data in result:
                if data["state"] == "open":
                    pr = PullRequest.get(data)
                    if pr.current_job:
                        continue
                    if not "Ready for CI build" in pr.labels:
                        continue
                    state = pr.get_state()
                    if state == "canceled" or state == "pending":
                        pr.start_job()

    def get_state(s):
        code, result = github.repos[s.base_full_name].statuses[s.head].get()
        if code==200:
            for data in result:
                if data["context"] == "RIOT CI":
                    if data["description"] == "The build has been canceled.":
                        return "canceled"
                    else:
                        return data["state"]
        else:
            log.warning("PullRequest: couldn't get statuses: code %s", code)

        return "unknown"

    def list():
        building = []
        queued = []
        finished = []
        for name, pr in PullRequest._map.items():
            job = pr.current_job
            if job:
                if job.state==JobState.finished:
                    finished.append((pr, job))
                elif job.state==JobState.running:
                    building.append((pr, job))
                else:
                    queued.append((pr, job))

        building = sorted(building, key=lambda x: x[1].time_started)
        queued = sorted(queued, key=lambda x: x[1].time_queued)
        finished = sorted(finished, key=lambda x: x[1].time_finished)

        return (building, queued, finished)

def handle_pull_request(request):
    data = json.loads(request.body.decode("utf-8"))
    pr_data = data["pull_request"]
    if pr_data["base"]["ref"] != "master":
        return

    #print(json.dumps(data, sort_keys=False, indent=4))
    action = data["action"]
    log.info("received PR action: %s", action)

    pr = PullRequest.get(pr_data).update()
    if action == "unlabeled":
        pr.remove_label(data["label"]["name"])
    elif action == "labeled":
        pr.add_label(data["label"]["name"])

def handle_push(request):
    data = json.loads(request.body.decode("utf-8"))

    log.info(json.dumps(data, sort_keys=False, indent=4))

github_handlers = {
        "pull_request" : handle_pull_request,
#        "push" : handle_push,
        }

github = GitHub(config.github_username, config.github_password)
queue = Queue()
ShellWorker(queue)
scripts_dir = os.getcwd() + "/scripts"

def shutdown():
    log.info("riot-ci: shutting down.")
    tornado.ioloop.IOLoop.instance().stop()

def sig_handler(sig, frame):
    log.warning('Caught signal: %s', sig)
    shutdown()

def startup_load_pull_requests():
    log.info("Loading pull requests...")
    for repo in config.repos:
        PullRequest.load(repo)
    log.info("All pull request loaded.")

def main():
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    log.info("riot CI initialized.")

    threading.Thread(target=startup_load_pull_requests, daemon=True).start()

    g = GithubWebhook(3000, PullRequest, github_handlers)
    g.run()

    # tornado loop ended

    PullRequest.cancel_all()
    log.info("riot CI shut down.")

if __name__ == "__main__":
    main()

