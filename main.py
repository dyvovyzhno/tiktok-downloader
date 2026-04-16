# main.py

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

import sentry_sdk
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from bot import bot, dp
from bot import handlers as _handlers  # noqa: F401  registers handlers
from settings import ENVIRONMENT, SENTRY_DSN

# Initialize Sentry
def init_sentry():
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        integrations=[
            AioHttpIntegration(),
            LoggingIntegration()
        ]
    )

async def main():
    try:
        logging.info('Started')
        await dp.start_polling()
    finally:
        logging.info('Exited')
        await bot.close()

if __name__ == '__main__':
    init_sentry()
    asyncio.run(main())