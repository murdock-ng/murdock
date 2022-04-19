# Murdock

A simple continuous integration server written in Python.

## Features

- Highly configurable
- REST API compatible with OpenAPI
- Per-repo fine tuning via .murdock.yml
- Live status of CI jobs via websockets
- Can comment results on pull-request

And many more not listed here.

# Requirements

Murdock is fully written in Python (requires >= 3.9) and is based on the
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
- `MURDOCK_WORK_DIR` corresponds to the base location where all jobs build results
will be stored. Each job is launched from a directory with the path
`<MURDOCK_WORK_DIR>/<job uid>` and all output data for a given job is located there
- `MURDOCK_SCRIPTS_DIR` corresponds to the location where the `run.sh` (default name)
is located. You can find examples [here](utils/run.sh) and
[here](utils/run-with-progress.sh). Use `MURDOCK_SCRIPT_NAME` env variable to
set a custom script name.
- `MURDOCK_HTML_DIR` corresponds to the location where the source code of
[murdock-html react app](https://github.com/riot-os/murdock-html) is located
- `MURDOCK_GITHUB_APP_CLIENT_ID` and `MURDOCK_GITHUB_APP_CLIENT_SECRET` corresponds
to the parameters of an Oauth application created on Github
([here](https://github.com/settings/applications/new)). By default, the
application should have the following parameters (but adapt if you use your own
domain):
  - Homepage URL: `http://localhost:8000`
  - Authorization callback URL: `http://localhost:8000`

You can specify a custom Docker image using the `MURDOCK_DOCKER_IMAGE`, if
eventually your murdock script requires extra tools.
This image should derive from `riot/murdock` to be sure Murdock is installed.

Build the React application:

```
$ docker-compose run webapp-build
```

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
$ docker-compose up mongo-dev
```

Launch the `uvicorn` server with the `--reload` option:

```
$ uvicorn murdock.main:app --reload --reload-dir murdock
```

To configure the application, you can specify environment variables
(the `GITHUB_*` vars are required):
- to the command line
- in a `.env.local` and add `ENV_FILE=.env.local` to the command line. See the
[.env](.env) as example.

Check the [default config](murdock/config.py).

# Testing

Unittests are available and performed using [pytest](https://pytest.org). The
full test suite can be launched using [Tox](https://tox.readthedocs.io):

Install Tox:

```
$ python 3 -m pip install tox
```

Run the tests:

```
$ tox
```
