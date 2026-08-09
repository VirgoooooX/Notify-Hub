"""UTC-normalized datetime type used by every persisted instant.

SQLite has no timezone-aware datetime storage.  ``DateTime(timezone=True)``
therefore returns naive values when using SQLite, which is dangerous when
those values are later compared with aware UTC values.  This type keeps the
existing ``DATETIME`` column shape while enforcing one contract at the ORM
boundary: values written to the database are aware UTC instants and values
read back are always aware UTC instants.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine


def restore_utc(value: datetime) -> datetime:
    """Restore a legacy SQLite row that predates ``UTCDateTime``."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware ORM datetime normalized to UTC.

    Naive values are rejected instead of guessing a server-local timezone.
    Existing SQLite rows without timezone metadata are interpreted as UTC;
    this preserves the historical storage convention used by Notify Hub.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[datetime]:
        # Keep the physical column as DateTime(timezone=True).  SQLite still
        # stores it as DATETIME, while PostgreSQL and other backends retain
        # native timezone support if introduced later.
        return dialect.type_descriptor(DateTime(timezone=True))

    @staticmethod
    def _normalize(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("persisted datetime must include timezone")
        return value.astimezone(UTC)

    def process_bind_param(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        return None if value is None else self._normalize(value)

    def process_result_value(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return restore_utc(value)
