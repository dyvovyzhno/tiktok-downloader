# main.py

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

import sentry_sdk
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from bot import bot, dp
from bot import handlers as _handlers  # noqa: F401  registers handlers
from bot.queue import start_workers
from settings import ENVIRONMENT, SENTRY_DSN

# Transient errors that should not be reported to Sentry.
_SENTRY_IGNORE = (
    "ClientConnectorError",
    "ServerDisconnectedError",
    "RemoteProtocolError",
    "Network is unreachable",
    "InvalidQueryID",
)


def _before_send(event, hint):
    """Drop noisy transient network / Telegram errors."""
    exc = hint.get("exc_info")
    if exc:
        exc_str = str(exc[1])
        exc_type = type(exc[1]).__name__
        if exc_type in _SENTRY_IGNORE or any(s in exc_str for s in _SENTRY_IGNORE):
            return None
    return event


# Initialize Sentry
def init_sentry():
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        before_send=_before_send,
        integrations=[
            AioHttpIntegration(),
            LoggingIntegration()
        ]
    )

async def main():
    try:
        logging.info('Started')
        await start_workers()
        await dp.start_polling()
    finally:
        logging.info('Exited')
        await bot.close()

if __name__ == '__main__':
    init_sentry()
    asyncio.run(main())