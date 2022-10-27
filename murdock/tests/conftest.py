import pytest
from xprocess import ProcessStarter
from murdock.murdock import Murdock

from prometheus_client import REGISTRY


@pytest.fixture
def mongo(xprocess):
    class Starter(ProcessStarter):
        pattern = "index build: done building index*"
        args = ["docker", "run", "--rm", "-p", "27017:27017", "mongo:4.2.16"]
        terminate_on_interrupt = True
        timeout = 10

    xprocess.ensure("mongo", Starter)
    yield
    xprocess.getinfo("mongo").terminate()


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
