import logging

from uvicorn.logging import ColourizedFormatter

from murdock.config import CONFIG

LOGGER = logging.getLogger("murdock")
LOGGER.setLevel(logging.getLevelName(CONFIG.murdock_log_level))

formatter = ColourizedFormatter(
    fmt=(
        "%(levelprefix)-8s %(asctime)-15s - "
        "%(filename)10s:%(lineno)-3d - %(message)s"
    )
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

LOGGER.addHandler(handler)
