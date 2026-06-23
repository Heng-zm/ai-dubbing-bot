"""Processing time estimation helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingEstimate:
    processing_seconds: int
    queue_wait_seconds: int

    @property
    def total_seconds(self) -> int:
        return self.processing_seconds + self.queue_wait_seconds


def estimate_processing_time(
    *,
    video_duration: float,
    subtitle_count: int,
    total_chars: int,
    queue_count: int = 0,
    provider: str = "edge",
) -> ProcessingEstimate:
    """Estimate end-to-end processing time.

    This is intentionally conservative for Render single-service deployments.
    Edge TTS is usually slower and can wait between requests; Azure is usually
    faster. Queue wait is estimated from the current number of queued jobs.
    """
    provider = (provider or "edge").lower()
    provider_factor = 0.75 if provider == "azure" else 0.95 if provider == "auto" else 1.0
    base = 28.0
    video_cost = max(0.0, float(video_duration)) * 0.65
    subtitle_cost = max(0, int(subtitle_count)) * (5.5 if provider != "azure" else 3.0)
    char_cost = max(0, int(total_chars)) * (0.018 if provider != "azure" else 0.010)
    ffmpeg_cost = 18.0 + max(0.0, float(video_duration)) * 0.35
    processing = int(math.ceil((base + video_cost + subtitle_cost + char_cost + ffmpeg_cost) * provider_factor))
    processing = max(45, processing)
    queue_count = max(0, int(queue_count))
    queue_wait = queue_count * max(45, int(processing * 0.75))
    return ProcessingEstimate(processing_seconds=processing, queue_wait_seconds=queue_wait)


def _kh_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 2:
        return "ប្រហែល 1 នាទី"
    if minutes <= 5:
        return f"ប្រហែល {minutes} នាទី"
    return f"ប្រហែល {minutes - 1}-{minutes + 1} នាទី"


def format_processing_estimate(estimate: ProcessingEstimate) -> str:
    if estimate.queue_wait_seconds > 0:
        return (
            f"ដំណើរការ: {_kh_duration(estimate.processing_seconds)}\n"
            f"រង់ចាំ Queue: {_kh_duration(estimate.queue_wait_seconds)}\n"
            f"សរុបរំពឹងទុក: {_kh_duration(estimate.total_seconds)}"
        )
    return f"រយៈពេលរំពឹងទុក: {_kh_duration(estimate.processing_seconds)}"
