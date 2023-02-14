import logging.config
import structlog
from murdock.config import GLOBAL_CONFIG

try:
    from cysystemd import journal
except ImportError:
    journal = None


# Pipeline used for all log messages
common_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.StackInfoRenderer(),
]

# Pipeline used with structlog messages
structlog_processors = common_processors + [
    structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
]

structlog.configure(
    processors=structlog_processors,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(GLOBAL_CONFIG.log_level)
    ),
    cache_logger_on_first_use=True,
)

log_handlers = {
    "console": {
        "formatter": "rich",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    },
}
if journal is not None:
    log_handlers.update(
        {
            "journal": {
                "formatter": "logfmt",
                "()": journal.JournaldLogHandler,
                "identifier": "murdock",
            }
        }
    )


stdlib_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "logfmt": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.LogfmtRenderer(bool_as_flag=True),
            "foreign_pre_chain": common_processors,
        },
        "rich": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
            "foreign_pre_chain": common_processors,
        },
    },
    "handlers": log_handlers,
    "loggers": {
        "": {
            "handlers": [GLOBAL_CONFIG.log_output],
            "level": GLOBAL_CONFIG.log_level,
            "propagate": True,
        },
    },
}
logging.config.dictConfig(stdlib_config)


LOGGER = structlog.getLogger("murdock")
