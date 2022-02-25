import pytest
from xprocess import ProcessStarter


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
