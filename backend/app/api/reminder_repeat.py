from datetime import datetime
from typing import Any

from app.domain.reminder_repeat import RepeatRule
from pydantic import Field


class RepeatInput(RepeatRule):
    max_attempts: int = Field(default=12, ge=1, le=12)
    stop_at: datetime | None = None

    def rule_config(self) -> dict[str, Any]:
        if self.type == "interval":
            return {}
        return self.model_dump(mode="json", exclude={"max_attempts", "stop_at"}, exclude_none=True)

    def interval_value(self) -> int | None:
        return (self.interval_seconds or 300) if self.type == "interval" else None
