#!/usr/bin/env python

import os
import pickle
import subprocess
import sys
import pprint
import json
import time
import shutil

from agithub.GitHub import GitHub

from os.path import abspath
import threading
from threading import Thread
from queue import Queue, Empty

from enum import Enum

from jobs import *
from github_webhook import GithubWebhook

import config

class ShellWorker(threading.Thread):
    def __init__(self, queue):
        threading.Thread.__init__(self)
        self.process = None
        self.queue = queue
        self.canceled = False
        self.job = None
        self.start()

    def run(s):
        print("ShellWorker: started.")
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
                print("ShellWorker: skipping finished job", job.name)
                s.queue.task_done()
                continue
            else:
                print("ShellWorker: building job", job.name)

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
                print(e)
            finally:
                output_file.close()

            print("ShellWorker: Job", s.job.name, "finished. result:", s.job.result)
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
    head = None

    def __init__(s, data):
        s.data = data
        s._map[data["pull_request"]["_links"]["html"]["href"]] = s
        s.current_job = None
        s.jobs = []
        s.labels = None

    def get(data):
        pull_url = data["pull_request"]["_links"]["html"]["href"]
        pr = PullRequest._map.get(pull_url)
        if pr:
            pr.data = data
            print("PR", pr.url, "updated")
        else:
            pr = PullRequest(data)
            pr.update_labels()
            print("PR", pr.url, "added")
        return pr

    def update(s):
        head_sha1 = s.data["pull_request"]["head"]["sha"]
        if head_sha1 != s.head:
            s.head = head_sha1
            print("PR", s.url, "has new head:", s.head)
            if "Ready for CI build" in s.labels:
                s.start_job()
        return s

    def cancel_job(s):
        if s.current_job:
            print("PR", s.url, "canceling build of commit", s.current_job.arg)
            s.current_job.cancel()
            s.current_job = None
        return s

    def start_job(s):
        s.cancel_job()

        env = { "CI_PULL_COMMIT" : s.commit,
                "CI_PULL_REPO" : s.repo,
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
        print("PR", s.url, "queueing build of commit", s.head)
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
            return s.data["pull_request"]["_links"]["html"]["href"]
        elif field == "base_repo":
            return s.data["pull_request"]["base"]["repo"]["clone_url"]
        elif field == "base_branch":
            return s.data["pull_request"]["base"]["ref"]
        elif field == "base_commit":
            return s.data["pull_request"]["base"]["sha"]
        elif field == "base_full_name":
            return s.data["pull_request"]["base"]["repo"]["full_name"]
        elif field == "nr":
            return s.data["pull_request"]["number"]
        elif field == "branch":
            return s.data["pull_request"]["head"]["ref"]
        elif field == "repo":
            return s.data["pull_request"]["head"]["repo"]["clone_url"]
        elif field == "commit":
            return s.data["pull_request"]["head"]["sha"]
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

def handle_pull_request(request):
    data = json.loads(request.body.decode("utf-8"))
    if data["pull_request"]["base"]["ref"] != "master":
        return

    #print(json.dumps(data, sort_keys=False, indent=4))
    action = data["action"]
    print("received PR action:", action)

    pr = PullRequest.get(data).update()
    if action == "unlabeled":
        pr.remove_label(data["label"]["name"])

    elif action == "labeled":
        pr.add_label(data["label"]["name"])

def handle_push(request):
    data = json.loads(request.body.decode("utf-8"))

    print(json.dumps(data, sort_keys=False, indent=4))

handlers = {
        "pull_request" : handle_pull_request,
#        "push" : handle_push,
        }

github = GitHub(config.github_username, config.github_password)
queue = Queue()
ShellWorker(queue)
scripts_dir = os.getcwd() + "/scripts"

def main():
    print("riot ci started.")
    g = GithubWebhook(3000, handlers)
    g.run()

if __name__ == "__main__":
    main()

