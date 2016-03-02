import tornado.ioloop
import tornado.web
import json

class GithubWebhook(object):
    def __init__(s, port, handler):
        s.secret = "__secret"
        s.port = port
        s.application = tornado.web.Application([
            (r"/", GithubWebhook.MainHandler),
            (r"/github", GithubWebhook.GithubWebhookHandler, dict(handler=handler)),
                ])
        s.application.listen(port)
        s.handlers = {}

    def run(s):
        tornado.ioloop.IOLoop.instance().start()

    class MainHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("...")

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
                print("unhandled github event:", hook_type)
