# bot/telemetry.py
#
# Programmatic OpenTelemetry setup — exports metrics to Dash0 / Grafana Cloud
# via OTLP gRPC. Falls back to no-op if OTEL is not configured.

import logging
from settings import (
    OTEL_ENDPOINT,
    OTEL_AUTH_TOKEN,
    OTEL_SERVICE_NAME,
)

_meter = None
_download_counter = None
_video_size_histogram = None
_error_counter = None
_watermark_counter = None


def _init():
    global _meter, _download_counter, _video_size_histogram, _error_counter, _watermark_counter

    if not OTEL_ENDPOINT or not OTEL_AUTH_TOKEN:
        logging.info("OTEL not configured — telemetry disabled")
        return

    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": OTEL_SERVICE_NAME})

        exporter = OTLPMetricExporter(
            endpoint=OTEL_ENDPOINT,
            headers=(("authorization", f"Bearer {OTEL_AUTH_TOKEN}"),),
            insecure=False,
        )

        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)

        _meter = metrics.get_meter("tiktok-downloader")

        _download_counter = _meter.create_counter(
            name="tiktok.downloads",
            description="Total video download attempts",
            unit="1",
        )
        _video_size_histogram = _meter.create_histogram(
            name="tiktok.video.size",
            description="Downloaded video size in bytes",
            unit="By",
        )
        _error_counter = _meter.create_counter(
            name="tiktok.errors",
            description="Errors during download or send",
            unit="1",
        )
        _watermark_counter = _meter.create_counter(
            name="tiktok.watermark.choices",
            description="User choices on watermark overlay (yes/no)",
            unit="1",
        )

        logging.info(f"OTEL telemetry enabled → {OTEL_ENDPOINT}")

    except Exception:
        logging.exception("Failed to initialize OpenTelemetry")


_init()


def record_download(chat_type: str, video_bytes: int):
    """Record a successful download."""
    if _download_counter:
        _download_counter.add(1, {"status": "ok", "chat_type": chat_type})
    if _video_size_histogram and video_bytes:
        _video_size_histogram.record(video_bytes, {"chat_type": chat_type})


def record_failure(chat_type: str, reason: str = "unknown"):
    """Record a failed download."""
    if _download_counter:
        _download_counter.add(1, {"status": "fail", "chat_type": chat_type})
    if _error_counter:
        _error_counter.add(1, {"reason": reason, "chat_type": chat_type})


def record_watermark_choice(with_watermark: bool, chat_type: str):
    """Record a user's watermark choice at the moment they tap the button."""
    if _watermark_counter:
        _watermark_counter.add(
            1, {"choice": "yes" if with_watermark else "no",
                "chat_type": chat_type}
        )
