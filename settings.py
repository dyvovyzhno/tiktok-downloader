from decouple import config

API_TOKEN = config('API_TOKEN',default=False)
SENTRY_DSN = config('SENTRY_DSN',default=False)
ENVIRONMENT = config('ENVIRONMENT', default='Local')
USER_AGENT = config('USER_AGENT',default=False)
ADMIN_ID = config('ADMIN_ID', default=0, cast=int)
ANALYTICS_EXCLUDE_IDS = set(
    int(x) for x in config('ANALYTICS_EXCLUDE_IDS', default='').split(',') if x.strip()
)

# OpenTelemetry / Dash0
OTEL_ENDPOINT = config('OTEL_ENDPOINT', default='')
OTEL_AUTH_TOKEN = config('OTEL_AUTH_TOKEN', default='')
OTEL_SERVICE_NAME = config('OTEL_SERVICE_NAME', default='tiktok-downloader')
