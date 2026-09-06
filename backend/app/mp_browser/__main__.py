"""Entrypoint for python -m app.mp_browser."""

import asyncio

from app.mp_browser.worker import run

if __name__ == "__main__":
    asyncio.run(run())
