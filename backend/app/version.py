from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def get_app_version() -> str:
    try:
        return version("notify-hub")
    except PackageNotFoundError:
        return "0.0.0+unknown"


APP_VERSION = get_app_version()
