"""Simple SRT parser and validator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.utils.time_utils import srt_timestamp_to_seconds

SRT_BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})[^\n]*\n"
    r"(.+?)(?=\n\s*\n|\Z)"
)


@dataclass
class SubtitleItem:
    index: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
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
        start = srt_timestamp_to_seconds(match.group(2))
        end = srt_timestamp_to_seconds(match.group(3))
        body = "\n".join(line.strip() for line in match.group(4).strip().splitlines()).strip()
        items.append(SubtitleItem(index=index, start=start, end=end, text=body))

    if not items:
        raise ValueError("No valid SRT blocks found")

    previous_end = -1.0
    for item in items:
        if item.end <= item.start:
            raise ValueError(f"Subtitle #{item.index} has invalid timing")
        if item.start < previous_end - 0.05:
            raise ValueError(f"Subtitle #{item.index} overlaps previous subtitle")
        if not item.text:
            raise ValueError(f"Subtitle #{item.index} has empty text")
        previous_end = item.end
    return items


def parse_srt_file(path: Path) -> List[SubtitleItem]:
    return parse_srt_text(read_text_file(path))


def validate_srt_file(path: Path, video_duration: float) -> List[SubtitleItem]:
    items = parse_srt_file(path)
    last_end = max(item.end for item in items)
    if last_end > video_duration + 0.5:
        raise ValueError("Subtitle timing exceeds video duration")
    return items
