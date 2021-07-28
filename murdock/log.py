import logging

from uvicorn.logging import ColourizedFormatter

from murdock.config import MURDOCK_LOG_LEVEL

LOGGER = logging.getLogger("murdock")
LOGGER.setLevel(MURDOCK_LOG_LEVEL)

formatter = ColourizedFormatter(
    fmt=(
        "%(levelprefix)-8s %(asctime)-15s - "
        "%(filename)10s:%(lineno)-3d - %(message)s"
    )
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

LOGGER.addHandler(handler)
