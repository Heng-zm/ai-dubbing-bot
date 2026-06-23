"""Simple SRT parser and validator with optional character-label support."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.config import settings
from app.services.runtime_settings import runtime_settings
from app.utils.text_utils import normalize_tts_text
from app.utils.time_utils import srt_timestamp_to_seconds

SRT_BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n"
    r"(.+?)(?=\n\s*\n|\Z)"
)

CHARACTER_BRACKET_RE = re.compile(
    r"^\s*[\[（(【{]\s*([^\]\）)】}:：]{1,40})\s*[\]\）)】}]\s*[:：\-–—]?\s*(.*)$",
    re.S,
)
CHARACTER_PREFIX_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 _\-]{0,32}|ប្រុស|ស្រី|បុរស|នារី|ក្មេងប្រុស|ក្មេងស្រី)\s*[:：]\s*(.+)$",
    re.S,
)


@dataclass
class SubtitleItem:
    index: int
    start: float
    end: float
    text: str
    character_label: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def split_character_label(raw_text: str) -> tuple[str | None, str]:
    """Extract an optional speaker label from a subtitle body.

    Supported examples:
    [boy] សួស្តី
    [girl]: ចាស៎
    boy: hello
    ស្រី: សួស្តី
    """
    text = raw_text.strip()
    match = CHARACTER_BRACKET_RE.match(text)
    if match:
        label = " ".join(match.group(1).split()).strip()
        spoken = match.group(2).strip()
        if label and spoken:
            return label, spoken
    match = CHARACTER_PREFIX_RE.match(text)
    if match:
        label = " ".join(match.group(1).split()).strip()
        spoken = match.group(2).strip()
        if label and spoken:
            return label, spoken
    return None, text


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_srt_text(text: str) -> List[SubtitleItem]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    items: List[SubtitleItem] = []
    for match in SRT_BLOCK_RE.finditer(normalized):
        index = int(match.group(1))
        start = srt_timestamp_to_seconds(match.group(2).replace(".", ","))
        end = srt_timestamp_to_seconds(match.group(3).replace(".", ","))
        body = "\n".join(line.strip() for line in match.group(4).strip().splitlines()).strip()
        character_label, spoken_body = split_character_label(body)
        spoken_body = normalize_tts_text(spoken_body)
        items.append(SubtitleItem(index=index, start=start, end=end, text=spoken_body, character_label=character_label))

    if not items:
        raise ValueError("invalid_srt")

    runtime = runtime_settings.cached()
    min_duration = float(runtime.get("min_subtitle_duration_seconds", settings.min_subtitle_duration_seconds))
    max_chars = int(runtime.get("max_subtitle_chars", settings.max_subtitle_chars))

    previous_end = -1.0
    for item in items:
        if item.end <= item.start:
            raise ValueError("invalid_srt_timing")
        if item.duration < min_duration:
            raise ValueError("subtitle_too_short")
        if item.start < previous_end - 0.05:
            raise ValueError("subtitle_overlap")
        if not item.text:
            raise ValueError("empty_subtitle")
        if len(item.text) > max_chars:
            raise ValueError("subtitle_too_long")
        previous_end = item.end
    return items


def parse_srt_file(path: Path) -> List[SubtitleItem]:
    return parse_srt_text(read_text_file(path))


def validate_srt_file(path: Path, video_duration: float) -> List[SubtitleItem]:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("invalid_srt")
    runtime = runtime_settings.cached()
    max_srt_size_mb = int(runtime.get("max_srt_size_mb", settings.max_srt_size_mb))
    if path.stat().st_size > max_srt_size_mb * 1024 * 1024:
        raise ValueError("srt_too_large")
    items = parse_srt_file(path)
    last_end = max(item.end for item in items)
    if video_duration > 0 and last_end > video_duration + 0.5:
        raise ValueError("srt_timing_exceeds_video")
    return items
