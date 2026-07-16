"""Trusted in-process plugin runtime.

Importing this package is deliberately side-effect free. Discovery and worker
startup are explicit application-lifecycle operations.
"""

from app.plugin_runtime.base import EventDraft, EventReceipt, NotifyPlugin, PluginRunResult
from app.plugin_runtime.context import (
    PluginContext,
    PluginReminderClient,
    PluginReminderDraft,
    PluginReminderReceipt,
)
from app.plugin_runtime.manifest import PluginManifest, PluginReminderPermissions
from app.plugin_runtime.registry import PluginRegistry

__all__ = [
    "EventDraft",
    "EventReceipt",
    "NotifyPlugin",
    "PluginContext",
    "PluginManifest",
    "PluginRegistry",
    "PluginReminderClient",
    "PluginReminderDraft",
    "PluginReminderPermissions",
    "PluginReminderReceipt",
    "PluginRunResult",
]
