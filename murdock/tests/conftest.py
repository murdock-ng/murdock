import pytest
from xprocess import ProcessStarter
from murdock.murdock import Murdock

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


@pytest.fixture
def murdock(request):
    args = {}
    marker = request.node.get_closest_marker("murdock_args")
    if marker is not None:
        args = marker.args[0]
    return Murdock(**args)