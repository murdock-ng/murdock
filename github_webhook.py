import tornado.httpserver
import tornado.ioloop
import tornado.web
import json
import os

from log import log

from util import config

config.set_default("url_prefix", r"")

class GithubWebhook(object):
    def __init__(s, port, prs, github_handlers):

        s.secret = "__secret"
        s.port = port
        s.application = tornado.web.Application([
#            (r"/", GithubWebhook.MainHandler),
            (config.url_prefix + r"/api/pull_requests", GithubWebhook.PullRequestHandler, dict(prs=prs)),
            (config.url_prefix + r"/github", GithubWebhook.GithubWebhookHandler, dict(handler=github_handlers)),
                ])
        s.server = tornado.httpserver.HTTPServer(s.application)
        s.server.listen(s.port)

    def run(s):
        log.info("tornado IOLoop started.")
        tornado.ioloop.IOLoop.instance().start()

    class MainHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("...")

    class PullRequestHandler(tornado.web.RequestHandler):
        def initialize(s, prs):
            s.prs = prs

        def get(self):
            def gen_pull_entry(pr, job, time, extra = None):
                res = extra or {}
                res.update({
                        "title" : pr.title,
                        "user" : pr.user,
                        "url" : pr.url,
                        "commit" : job.arg,
                        "since" : time,
                        })
                return res

            self.set_header("Content-Type", 'application/json; charset="utf-8"')
            self.set_header("Access-Control-Allow-Credentials", "false")
            self.set_header("Access-Control-Allow-Origin", "*")

            building, queued, finished = self.prs.list()
            response = {}

            if building:
                _building = []
                for pr, job in building:
                    _building.append(
                            gen_pull_entry(pr, job, job.time_started))
                response['building'] = _building

            if queued:
                _queued = []
                for pr, job in queued:
                    _queued.append(
                            gen_pull_entry(pr, job, job.time_queued))

                response['queued'] = _queued

            if finished:
                _finished = []
                for pr, job in finished:
                    _finished.append(
                            gen_pull_entry(pr, job, job.time_finished,
                            { "output_url" :
                                os.path.join(config.http_root,
                                    pr.base_full_name, str(pr.nr), job.arg, "output.html"),
                                "result" : job.result.name,
                                "runtime" : (job.time_finished - job.time_started)
                                }))

                response['finished'] = _finished

            self.write(json.dumps(response, sort_keys=False, indent=4))

    class GithubWebhookHandler(tornado.web.RequestHandler):
        def initialize(s, handler):
            s.handler = handler

        def post(s):
            s.write("ok")
            hook_type = s.request.headers.get('X-Github-Event')

            handler = s.handler.get(hook_type)
            if handler:
                handler(s.request)
            else:
                log.warning("unhandled github event: %s", hook_type)
