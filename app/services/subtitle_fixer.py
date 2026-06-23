"""Auto Subtitle Fixer for imperfect SRT files.

The fixer is intentionally conservative: it repairs common formatting mistakes
without rewriting dialogue. It produces a normalized .srt file that can be parsed
by the normal validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.services.srt_parser import parse_srt_text, read_text_file
from app.utils.time_utils import srt_timestamp_to_seconds

TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?P<ms>[,.]\d{1,3})?"
)
TIMING_LINE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)\s*"
    r"(?:[-–—]{1,3}>|→|➡|to|until|ដល់)\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)",
    re.I,
)
NUMBERING_RE = re.compile(r"^\s*\d+[\.)]?\s*$")
CODE_FENCE_RE = re.compile(r"^```(?:srt|text)?\s*|\s*```$", re.I | re.M)


@dataclass
class FixReport:
    fixed: bool = False
    changed_text: bool = False
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    original_block_count: int = 0
    fixed_block_count: int = 0

    def add_fix(self, message: str) -> None:
        if message not in self.fixes:
            self.fixes.append(message)
        self.fixed = True

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


@dataclass
class _RawBlock:
    start: float
    end: float
    text: str


def seconds_to_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _normalize_timestamp(value: str, report: FixReport) -> str:
    match = TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("invalid_srt_timing")
    h = int(match.group("h"))
    m = int(match.group("m"))
    s = int(match.group("s"))
    ms_raw = match.group("ms")
    ms = 0
    if ms_raw:
        digits = ms_raw[1:]
        ms = int(digits.ljust(3, "0")[:3])
        if ms_raw.startswith(".") or len(digits) != 3:
            report.add_fix("បានកែ format milliseconds/timestamp")
    else:
        report.add_fix("បានបន្ថែម milliseconds ទៅ timing")
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean_text(raw: str, report: FixReport) -> str:
    # Remove HTML-ish line breaks and common Gemini/Markdown wrappers while keeping speaker labels.
    text = raw.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("\ufeff")
        if not cleaned:
            continue
        # Remove common bullet prefix Gemini sometimes adds before subtitle text.
        cleaned = re.sub(r"^[-*•]\s+", "", cleaned)
        lines.append(cleaned)
    if not lines:
        raise ValueError("empty_subtitle")
    fixed_text = "\n".join(lines).strip()
    if fixed_text != raw.strip():
        report.add_fix("បានសម្អាត blank lines/spacing ក្នុង subtitle")
    return fixed_text


def _extract_blocks(text: str, report: FixReport) -> list[_RawBlock]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = CODE_FENCE_RE.sub("", normalized).strip()
    if normalized != text.strip():
        report.add_fix("បានដក Markdown/code fence ចេញ")

    lines = normalized.splitlines()
    blocks: list[_RawBlock] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Skip numbering line if present. Missing/wrong numbering will be regenerated.
        if NUMBERING_RE.match(line):
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()
        else:
            report.add_fix("បានបន្ថែម/កែលេខរៀង SRT")

        timing = TIMING_LINE_RE.search(line)
        if not timing:
            # Sometimes Gemini prefixes timing lines with a bullet or text. Try next line.
            i += 1
            continue

        start_ts = _normalize_timestamp(timing.group("start"), report)
        end_ts = _normalize_timestamp(timing.group("end"), report)
        start = srt_timestamp_to_seconds(start_ts)
        end = srt_timestamp_to_seconds(end_ts)
        i += 1

        text_lines: list[str] = []
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                i += 1
                break
            if NUMBERING_RE.match(next_line) and i + 1 < len(lines) and TIMING_LINE_RE.search(lines[i + 1]):
                break
            if TIMING_LINE_RE.search(next_line):
                # Missing blank line between blocks. Let outer loop process this line.
                report.add_fix("បានបន្ថែមចន្លោះរវាង subtitle blocks")
                break
            text_lines.append(lines[i])
            i += 1

        blocks.append(_RawBlock(start=start, end=end, text=_clean_text("\n".join(text_lines), report)))

    report.original_block_count = len(blocks)
    if not blocks:
        raise ValueError("invalid_srt")
    return blocks


def _normalize_timing(
    blocks: list[_RawBlock],
    *,
    video_duration: float,
    max_overlap_seconds: float,
    max_video_overrun_seconds: float,
    min_gap_seconds: float,
    report: FixReport,
) -> list[_RawBlock]:
    fixed: list[_RawBlock] = []
    previous_end = 0.0
    min_duration = max(0.15, float(settings.min_subtitle_duration_seconds))

    for block in blocks:
        start = max(0.0, float(block.start))
        end = max(0.0, float(block.end))
        if end <= start:
            raise ValueError("invalid_srt_timing")

        if fixed and start < previous_end + min_gap_seconds:
            overlap = previous_end + min_gap_seconds - start
            if overlap <= max_overlap_seconds:
                duration = end - start
                start = previous_end + min_gap_seconds
                end = start + duration
                report.add_fix("បានកែ timing ជាន់គ្នាតិចតួច")
            else:
                raise ValueError("subtitle_overlap")

        if end - start < min_duration:
            end = start + min_duration
            report.add_fix("បានបន្ថែមរយៈពេល subtitle ខ្លីពេក")

        if video_duration > 0 and end > video_duration:
            overrun = end - video_duration
            if overrun <= max_video_overrun_seconds:
                end = video_duration
                if end <= start:
                    start = max(0.0, end - min_duration)
                report.add_fix("បានកាត់ timing ចុងក្រោយឱ្យស្មើរយៈពេលវីដេអូ")
            else:
                raise ValueError("srt_timing_exceeds_video")

        fixed.append(_RawBlock(start=start, end=end, text=block.text))
        previous_end = end

    report.fixed_block_count = len(fixed)
    return fixed


def _render_srt(blocks: Iterable[_RawBlock]) -> str:
    parts: list[str] = []
    for idx, block in enumerate(blocks, start=1):
        parts.append(
            f"{idx}\n{seconds_to_srt_timestamp(block.start)} --> {seconds_to_srt_timestamp(block.end)}\n{block.text.strip()}"
        )
    return "\n\n".join(parts).strip() + "\n"


def fix_srt_text(
    text: str,
    *,
    video_duration: float = 0.0,
    max_overlap_seconds: float = 1.2,
    max_video_overrun_seconds: float = 2.0,
    min_gap_seconds: float = 0.05,
) -> tuple[str, FixReport]:
    """Return normalized SRT text and a report of safe fixes applied."""
    report = FixReport()
    blocks = _extract_blocks(text, report)
    blocks = _normalize_timing(
        blocks,
        video_duration=video_duration,
        max_overlap_seconds=max_overlap_seconds,
        max_video_overrun_seconds=max_video_overrun_seconds,
        min_gap_seconds=min_gap_seconds,
        report=report,
    )
    fixed_text = _render_srt(blocks)

    # Validate normalized output using the production parser.
    parse_srt_text(fixed_text)

    original_normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    report.changed_text = fixed_text.strip() != original_normalized
    if report.changed_text:
        report.fixed = True
    if report.fixed and "បានកែលេខរៀង SRT" not in report.fixes:
        # The renderer always renumbers blocks. Add this once when text changed due numbering/timing style.
        original_numbers = re.findall(r"(?m)^\s*\d+\s*$", original_normalized)
        if len(original_numbers) != len(blocks) or any(int(n.strip()) != i for i, n in enumerate(original_numbers, start=1)):
            report.add_fix("បានកែលេខរៀង SRT")
    return fixed_text, report


def fix_srt_file(
    path: Path,
    *,
    video_duration: float = 0.0,
    max_overlap_seconds: float = 1.2,
    max_video_overrun_seconds: float = 2.0,
    min_gap_seconds: float = 0.05,
) -> FixReport:
    """Normalize an SRT file in-place and return a report."""
    original = read_text_file(path)
    fixed_text, report = fix_srt_text(
        original,
        video_duration=video_duration,
        max_overlap_seconds=max_overlap_seconds,
        max_video_overrun_seconds=max_video_overrun_seconds,
        min_gap_seconds=min_gap_seconds,
    )
    if report.changed_text:
        path.write_text(fixed_text, encoding="utf-8")
    return report


def format_fix_report_khmer(report: FixReport, *, max_items: int = 5) -> str:
    if not report.fixed:
        return "• SRT ត្រឹមត្រូវ — មិនចាំបាច់កែស្វ័យប្រវត្តិទេ ✅"
    items = report.fixes[:max_items]
    more = len(report.fixes) - len(items)
    lines = [f"• {item}" for item in items]
    if more > 0:
        lines.append(f"• និងការកែផ្សេងទៀត {more} ចំណុច")
    return "\n".join(lines)
