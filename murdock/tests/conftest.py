import time
import pytest
from xprocess import ProcessStarter
from murdock.murdock import Murdock
from murdock.database import Database
from unittest import mock

from prometheus_client import REGISTRY


@pytest.fixture
def mongodb(xprocess):
    class Starter(ProcessStarter):
        pattern = "index build: done building index*"
        args = ["docker", "run", "--rm", "-p", "27017:27017", "mongo:4.2.16"]
        terminate_on_interrupt = True
        timeout = 10

    xprocess.ensure("mongo", Starter)
    yield
    xprocess.getinfo("mongo").terminate()


@pytest.fixture
def postgresql(xprocess):
    class Starter(ProcessStarter):
        pattern = ".*database system is ready to accept connections"
        args = ["docker", "run", "--rm", "-e", "POSTGRES_PASSWORD=hunter2",
                "-e", "POSTGRES_USER=murdock", "-e", "PGDATA=/tmp/postgres",
                "-p", "5432:5432", "postgres:13"]
        terminate_on_interrupt = True
        timeout = 10

    xprocess.ensure("postgres", Starter)
    time.sleep(1)
    yield
    xprocess.getinfo("postgres").terminate()


# Flush the prometheus collector registry after tests
@pytest.fixture
def clear_prometheus_registry():
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        REGISTRY.unregister(collector)


@pytest.fixture
def murdock(request, clear_prometheus_registry):
    args = {}
    marker = request.node.get_closest_marker("murdock_args")
    if marker is not None:
        args = marker.args[0]
    return Murdock(**args)


@pytest.fixture
def murdock_mockdb(murdock):
    mock_db = mock.Mock(spec=Database)
    murdock.db = mock_db
    return murdock
