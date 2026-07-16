from __future__ import annotations

import enum


class ReminderDraftError(ValueError):
    """Stable domain error raised for invalid reminder draft operations."""


class ReminderDraftNotFound(ReminderDraftError):
    pass


class ReminderDraftPermissionDenied(ReminderDraftError):
    pass


class ReminderDraftExpired(ReminderDraftError):
    pass


class InvalidReminderDraftTransition(ReminderDraftError):
    pass


class ReminderDraftStatus(str, enum.Enum):
    EDITING = "editing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReminderDraftParseMethod(str, enum.Enum):
    RULES = "rules"
    AI = "ai"
    MANUAL = "manual"


class ReminderDraftSourceType(str, enum.Enum):
    WECOM_TEXT = "wecom_text"
    WEB = "web"
    API = "api"


_ALLOWED_TRANSITIONS: dict[ReminderDraftStatus, frozenset[ReminderDraftStatus]] = {
    ReminderDraftStatus.EDITING: frozenset(
        {
            ReminderDraftStatus.EDITING,
            ReminderDraftStatus.AWAITING_CONFIRMATION,
            ReminderDraftStatus.CANCELLED,
            ReminderDraftStatus.EXPIRED,
        }
    ),
    ReminderDraftStatus.AWAITING_CONFIRMATION: frozenset(
        {
            ReminderDraftStatus.EDITING,
            ReminderDraftStatus.AWAITING_CONFIRMATION,
            ReminderDraftStatus.CONFIRMED,
            ReminderDraftStatus.CANCELLED,
            ReminderDraftStatus.EXPIRED,
        }
    ),
    ReminderDraftStatus.CONFIRMED: frozenset(),
    ReminderDraftStatus.CANCELLED: frozenset(),
    ReminderDraftStatus.EXPIRED: frozenset(),
}


def validate_reminder_draft_transition(
    current: ReminderDraftStatus,
    target: ReminderDraftStatus,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidReminderDraftTransition(
            f"cannot transition reminder draft from {current.value} to {target.value}"
        )
