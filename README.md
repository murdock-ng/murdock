[![CI][ci-badge]][ci-link]
[![Coverage][coverage-badge]][coverage-link]

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
[MongoDB](https://www.mongodb.com/) database.

We recommend that you use
[docker-compose](https://docs.docker.com/compose/#compose-documentation) to
deploy Murdock on a server.

Murdock uses Webhook to interact with GitHub, so we recommend that you read
the [Github WebHook documentation](https://docs.github.com/en/developers/webhooks-and-events/webhooks/creating-webhooks).

# Deployment

A default Docker based deployment can be performed using `make`:

Build the React application:

```
$ make
```

This command will copy the provided [.env.example](.env.example) file to a
default .env so that it contains good defaults, then clone the
[murdock-html](https://github.com/murdock-ng/murdock-html) repository, build
the frontend web application and finally launch the Docker services (mongo
database, web frontend and Murdock API server).
Once deployed, by default, the UI is available at http://localhost:8000.

# Development

For local development, we recommend that you use a Python
[virtualenv](https://virtualenv.pypa.io/en/latest/).

Install all Murdock dependencies:

```
$ python3 -m pip install -r requirements.txt
```

Launch the MongoDB service:

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
- in a .env file (see [.env.example](.env.example) for a settings overview)
- in a `.env.local` and add `ENV_FILE=.env.local` to the command line. See the
[.env](.env) as example.

To get a complete list of configuration options, check the
[Murdock config code](murdock/config.py).

# Testing

Unittests are available and performed using [pytest](https://pytest.org). The
full test suite can be launched using [Tox](https://tox.readthedocs.io):

Install Tox:

```
$ python3 -m pip install tox
```

Run the tests:

```
$ tox
```


[ci-badge]: https://github.com/murdock-ng/murdock/workflows/CI/badge.svg
[ci-link]: https://github.com/murdock-ng/murdock/actions?query=workflow%3ACI+branch%3Amain
[coverage-badge]: https://codecov.io/gh/murdock-ng/murdock/branch/main/graph/badge.svg?token=86RDZ29XKQ
[coverage-link]: https://codecov.io/gh/murdock-ng/murdock
