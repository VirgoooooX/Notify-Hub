import json
import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

SENSITIVE_KEYS = re.compile(
    r"authorization|cookie|api[-_]?key|access[-_]?token|refresh[-_]?token|secret|password|"
    r"ciphertext|encoding[-_]?aes[-_]?key|master[-_]?key|phone|mobile|corp[-_]?id",
    re.IGNORECASE,
)
SENSITIVE_QUERY_PARAMS = re.compile(
    r"(?i)([?&](?:access[-_]?token|refresh[-_]?token|corp(?:[-_]?id|secret)|"
    r"api[-_]?key|secret|password|token|authorization|cookie|"
    r"encoding[-_]?aes[-_]?key|msg[-_]?signature|signature|code)=)"
    r"([^&#\s\"']*)"
)
SENSITIVE_DEPENDENCY_LOGGERS = ("httpx", "httpcore", "uvicorn.access")


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        value = re.sub(r"nfy_[A-Za-z0-9_-]{8,}", "nfy_[REDACTED]", value)
        value = SENSITIVE_QUERY_PARAMS.sub(r"\1[REDACTED]", value)
    return value


class SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(item) for item in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {key: redact(value, str(key)) for key, value in record.args.items()}
        return True


def _suppress_sensitive_dependency_logs() -> None:
    for logger_name in SENSITIVE_DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def redact_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    redacted = redact(event_dict)
    return redacted if isinstance(redacted, Mapping) else {"event": redacted}


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper(), force=True)
    for handler in logging.getLogger().handlers:
        handler.addFilter(SensitiveLogFilter())
    _suppress_sensitive_dependency_logs()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.JSONRenderer(serializer=json.dumps),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
