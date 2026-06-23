"""Small Telegram UI helpers for cleaner Khmer bot messages."""

from __future__ import annotations

from typing import Any

from app.states import (
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_PROCESSING,
    TASK_QUEUED,
    TASK_WAITING_SRT,
    TASK_WAITING_VIDEO,
)


STATUS_LABELS: dict[str, str] = {
    TASK_WAITING_VIDEO: "កំពុងរង់ចាំវីដេអូ",
    TASK_WAITING_SRT: "កំពុងរង់ចាំ SRT",
    TASK_QUEUED: "នៅក្នុង Queue",
    TASK_PROCESSING: "កំពុងដំណើរការ",
    TASK_COMPLETED: "រួចរាល់",
    TASK_FAILED: "បរាជ័យ",
    TASK_CANCELLED: "បានបោះបង់",
}

STATUS_EMOJI: dict[str, str] = {
    TASK_WAITING_VIDEO: "🎬",
    TASK_WAITING_SRT: "📝",
    TASK_QUEUED: "⏳",
    TASK_PROCESSING: "⚙️",
    TASK_COMPLETED: "✅",
    TASK_FAILED: "❌",
    TASK_CANCELLED: "🚫",
}


def progress_bar(percent: Any, width: int = 10) -> str:
    """Return a compact text progress bar that displays well in Telegram."""
    try:
        value = max(0, min(100, int(float(percent))))
    except Exception:
        value = 0
    filled = round((value / 100) * width)
    return "█" * filled + "░" * (width - filled)


def percent_line(percent: Any) -> str:
    try:
        value = max(0, min(100, int(float(percent))))
    except Exception:
        value = 0
    return f"{progress_bar(value)} {value}%"


def status_label(status: Any) -> str:
    raw = str(status or "unknown")
    return STATUS_LABELS.get(raw, raw)


def status_emoji(status: Any) -> str:
    raw = str(status or "unknown")
    return STATUS_EMOJI.get(raw, "ℹ️")


def bool_badge(value: Any) -> str:
    raw = str(value).strip().lower()
    is_true = value is True or raw in {"true", "1", "yes", "on"}
    return "បើក ✅" if is_true else "បិទ ❌"


def step_title(step: int, total: int, title: str) -> str:
    return f"ជំហាន {step}/{total} • {title}"
