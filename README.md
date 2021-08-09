# murdock

A simple CI (continuous integration) server written in Python.
Developed for RIOT (riot-os.org).

# Requirements

Murdock is fully written in Python (requires >= 3.8) and is based on the
[FastAPI](https://fastapi.tiangolo.com/) framework for the REST API.
In order to keep the history of completed jobs, they are stored in a
[MongoDb](https://www.mongodb.com/) database.

We recommend that you use
[docker-compose](https://docs.docker.com/compose/#compose-documentation) to
deploy Murdock on a server.

Murdock uses Webhook to interact with GitHub, so we recommend that you read
the [Github WebHook documentation](https://docs.github.com/en/developers/webhooks-and-events/webhooks/creating-webhooks).

# Deployment

First you have to adapt the [.env](.env) file with your project setup:
- It is important that the variables `GITHUB_REPO` (in the form `orga/repo`) ,
`GITHUB_WEBHOOK_SECRET` and `GITHUB_API_TOKEN` are correctly set.
- `MURDOCK_ROOT_DIR` corresponds to the base location where all build jobs output
will be stored. Each job is launched from a directory with the path
`<MURDOCK_ROOT_DIR>/<orga>/<repo>/<pr number>/<commit hash>` and all output data
for a given job is located there
- `MURDOCK_SCRIPTS_DIR` corresponds to the location where the `build.sh` is
located. You can find examples [here](utils/buils.sh) and
[here](scripts.example/build.sh.example).

Build the base Docker image:

```
$ docker-compose build
```

You can specify a custom Docker image using the `MURDOCK_DOCKER_IMAGE`, if
eventually your `build.sh` requires extra tools. This
image should derive from `riot/murdock` to be sure Murdock is installed.

Launch Murdock and MongoDb services:

```
$ docker-compose up
```

# Development

For local development, we recommend that you use a Python
[virtualenv](https://virtualenv.pypa.io/en/latest/).

Install all Murdock dependencies:

```
$ python3 -m pip install -r requirements.txt
```

Launch the MongoDb service:

```
$ docker-compose up mongo
```

Launch the `uvicorn` server with the `--reload` option:

```
$ uvicorn murdock.main:app --reload
```

You can specify environment variables to the command line (the `GITHUB_*`
vars are required). Check the [default config](murdock/config.py).
