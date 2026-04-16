from decouple import config

API_TOKEN = config('API_TOKEN',default=False)
SENTRY_DSN = config('SENTRY_DSN',default=False)
ENVIRONMENT = config('ENVIRONMENT', default='Local')
USER_AGENT = config('USER_AGENT',default=False)
