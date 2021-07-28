import logging
import os
import secrets


MURDOCK_BASE_URL = os.getenv("MURDOCK_BASE_URL", "https://ci.riot-os.org")
MURDOCK_ROOT_DIR = os.getenv("MURDOCK_ROOT_DIR", "/var/lib/murdock-data")
MURDOCK_SCRIPTS_DIR = os.getenv(
    "MURDOCK_SCRIPTS_DIR", "/var/lib/murdock-scripts"
)
MURDOCK_USE_SECURE_API = int(os.getenv("MURDOCK_USE_SECURE_API", 0)) == 1
MURDOCK_API_SECRET = secrets.token_urlsafe(32)
MURDOCK_NUM_WORKERS = int(os.getenv("MURDOCK_NUM_WORKERS", 1))
MURDOCK_LOG_LEVEL = logging.getLevelName(
    os.getenv("MURDOCK_LOG_LEVEL", "DEBUG")
)
MURDOCK_DB_HOST = os.getenv("MURDOCK_DB_HOST", "localhost")
MURDOCK_DB_PORT = int(os.getenv("MURDOCK_DB_PORT", 27017))
MURDOCK_DB_NAME = os.getenv("MURDOCK_DB_NAME", "murdock")
MURDOCK_MAX_FINISHED_LENGTH_DEFAULT = int(os.getenv(
    "MURDOCK_MAX_FINISHED_LENGTH_DEFAULT", 25
))

GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
GITHUB_API_USER = os.getenv("GITHUB_API_USER")
GITHUB_API_TOKEN = os.getenv("GITHUB_API_TOKEN")

CI_CANCEL_ON_UPDATE = int(os.getenv("CI_CANCEL_ON_UPDATE", 1)) == 1
CI_READY_LABEL = os.getenv("CI_READY_LABEL", "CI: ready for build")
CI_FASTTRACK_LABELS = os.getenv(
    "CI_FASTTRACK_LABELS", "CI: skip compile test;Process: release backport"
).split(";")

CONFIG_MSG = f"""
Murdock Settings:
\tMURDOCK_BASE_URL........ .: {MURDOCK_BASE_URL}
\tMURDOCK_ROOT_DIR..........: {MURDOCK_ROOT_DIR}
\tMURDOCK_SCRIPTS_DIR.......: {MURDOCK_SCRIPTS_DIR}
\tMURDOCK_USE_SECURE_API....: {MURDOCK_USE_SECURE_API}
\tMURDOCK_API_SECRET........: {MURDOCK_API_SECRET}
\tMURDOCK_NUM_WORKERS.......: {MURDOCK_NUM_WORKERS}
\tMURDOCK_LOG_LEVEL.........: {MURDOCK_LOG_LEVEL}

Github Settings:
\tGITHUB_REPO...............: {GITHUB_REPO}
\tGITHUB_WEBHOOK_SECRET.....: {GITHUB_WEBHOOK_SECRET}
\tGITHUB_API_USER...........: {GITHUB_API_USER}
\tGITHUB_API_TOKEN..........: {GITHUB_API_TOKEN}

CI Settings:
\tCI_CANCEL_ON_UPDATE.......: {CI_CANCEL_ON_UPDATE}
\tCI_READY_LABEL............: {CI_READY_LABEL}
\tCI_FASTTRACK_LABELS.......: {CI_FASTTRACK_LABELS}
"""


def check_config():
    msg = ""
    if not os.path.exists(MURDOCK_ROOT_DIR):
        msg = "'MURDOCK_ROOT_DIR' doesn't exists"
    if not os.path.exists(MURDOCK_SCRIPTS_DIR):
        msg = "'MURDOCK_SCRIPTS_DIR' doesn't exists"
    if not GITHUB_REPO:
        msg = "'GITHUB_REPO' is not set"
    if not GITHUB_WEBHOOK_SECRET:
        msg = "'GITHUB_WEBHOOK_SECRET' is not set"
    if not GITHUB_API_USER:
        msg = "'GITHUB_API_USER' is not set"
    if not GITHUB_API_TOKEN:
        msg = "'GITHUB_API_TOKEN' is not set"
    return msg
