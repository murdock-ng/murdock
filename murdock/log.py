import logging

from murdock.config import MURDOCK_LOG_LEVEL

LOGGER = logging.getLogger("murdock")
LOGGER.setLevel(MURDOCK_LOG_LEVEL)
formatter = logging.Formatter(
    "%(asctime)-15s - %(levelname)s - %(filename)s:%(lineno)-3d - %(message)s"
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

LOGGER.addHandler(handler)
