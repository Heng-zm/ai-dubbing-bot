"""User dubbing flow handlers.

This module owns the user-facing dubbing workflow:
1. receive video
2. receive and validate SRT
3. show a subtitle preview before processing
4. enqueue confirmed tasks and show queue position
5. allow users to retry failed tasks when temp files still exist
"""

from __future__ import annotations

import uuid
from html import escape
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config import settings
from app.services.estimate_service import ProcessingEstimate, estimate_processing_time, format_processing_estimate
from app.services.logger_service import log_db, logger
from app.services.redis_service import redis_service
from app.services.runtime_settings import runtime_settings
from app.services.srt_parser import SubtitleItem, validate_srt_file
from app.services.subtitle_fixer import FixReport, fix_srt_file, format_fix_report_khmer
from app.services.supabase_service import supabase_service
from app.services.task_service import update_task_status
from app.services.voice_service import summarize_character_voices
from app.services.telegram_files import download_and_validate_video, download_srt
from app.states import (
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_WAITING_CONFIRM,
    STATE_WAITING_SRT,
    STATE_WAITING_VIDEO,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_PROCESSING,
    TASK_QUEUED,
    TASK_WAITING_SRT,
    VOICE_LABELS,
)
from app.utils.file_utils import delete_file
from app.utils.text_utils import truncate
from app.utils.time_utils import seconds_to_readable
from app.utils.telegram_ui import percent_line, status_emoji, status_label, step_title




def _format_srt_instruction_message() -> str:
    """Return the improved Khmer instruction shown after the video is accepted.

    This message is sent with Telegram HTML parse mode. The Gemini prompt is
    wrapped in <pre> so Telegram displays it in a monospace/code block and the
    user can copy it more easily.
    """
    gemini_prompt = (
        "Transcribe this video into a valid .srt subtitle file.\n"
        "Translate all dialogue into natural Khmer.\n\n"
        "Rules:\n"
        "- Keep accurate timestamps from the video.\n"
        "- Use short, natural Khmer subtitle lines.\n"
        "- Keep correct SRT numbering: 1, 2, 3...\n"
        "- Use SRT timestamp format: 00:00:01,000 --> 00:00:03,000\n"
        "- Do not add explanations, markdown, or extra text.\n"
        "- Output only the final SRT content."
    )
    prompt_block = f"<pre>{escape(gemini_prompt)}</pre>"
    return (
        "✅ វីដេអូបានទទួលហើយ\n\n"
        f"{step_title(3, 4, 'ផ្ញើឯកសារ SRT')}\n\n"
        "សូមផ្ញើឯកសារ Subtitle .srt ជា Document។\n"
        "បន្ទាប់ពីផ្ញើ Bot នឹងបង្ហាញ Preview ឱ្យពិនិត្យ មុនចាប់ផ្តើមដំណើរការ។\n\n"
        "📌 ប្រសិនបើអ្នកមិនទាន់មានឯកសារ SRT៖\n"
        "1️⃣ ចូលទៅ Gemini: https://gemini.google.com/app\n"
        "2️⃣ Upload វីដេអូរបស់អ្នកទៅ Gemini\n"
        "3️⃣ Copy prompt ខាងក្រោម ហើយ Paste ចូល Gemini៖\n\n"
        f"{prompt_block}\n\n"
        "4️⃣ Copy លទ្ធផល SRT ពី Gemini → Save ជា file .srt\n"
        "5️⃣ ផ្ញើ file .srt នោះមក Bot ជា Document។"
    )


def _srt_instruction_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌐 បើក Gemini", url="https://gemini.google.com/app")],
            [InlineKeyboardButton("❌ បោះបង់", callback_data=f"dubbing:cancel:{task_id}")],
        ]
    )

def _khmer_video_error(code: str) -> str:
    runtime = runtime_settings.cached()
    max_size = runtime.get("max_video_size_mb", settings.max_video_size_mb)
    max_duration = runtime.get("max_video_duration_seconds", settings.max_video_duration_seconds)
    messages = {
        "unsupported_video_format": (
            "❌ Format វីដេអូមិនត្រឹមត្រូវទេ។\n\n"
            "សូមផ្ញើវីដេអូជា mp4, mov, mkv ឬ webm ប៉ុណ្ណោះ។"
        ),
        "video_too_large": (
            "❌ វីដេអូធំពេក។\n\n"
            f"សូមផ្ញើឯកសារមិនលើស {max_size}MB។"
        ),
        "video_too_long": (
            "❌ វីដេអូវែងពេក។\n\n"
            f"សូមផ្ញើវីដេអូដែលមានរយៈពេលមិនលើស {max_duration} វិនាទី។"
        ),
        "no_video": "សូមផ្ញើវីដេអូ ឬឯកសារវីដេអូ។",
    }
    return messages.get(code, "សូមទោស មិនអាចទទួលវីដេអូនេះបានទេ។ សូមព្យាយាមម្តងទៀត។")

def _khmer_srt_error(code: str) -> str:
    runtime = runtime_settings.cached()
    max_srt_size = runtime.get("max_srt_size_mb", settings.max_srt_size_mb)
    messages = {
        "no_srt": "សូមផ្ញើឯកសារ .srt ជា Document។",
        "not_srt": "❌ ឯកសារមិនមែនជា .srt ទេ។ សូមផ្ញើឯកសារ Subtitle ដែលបញ្ចប់ដោយ .srt។",
        "srt_too_large": f"❌ ឯកសារ SRT ធំពេក។ សូមផ្ញើមិនលើស {max_srt_size}MB។",
        "invalid_srt": "❌ Format SRT មិនត្រឹមត្រូវ។ សូមពិនិត្យលេខរៀង, timing និងអត្ថបទ។",
        "invalid_srt_timing": "❌ Timing នៅក្នុង SRT មិនត្រឹមត្រូវ។",
        "subtitle_overlap": "❌ Timing subtitle មានការជាន់គ្នា។ សូមកែ SRT ហើយផ្ញើម្តងទៀត។",
        "subtitle_too_short": "❌ Subtitle ខ្លីពេក។ សូមកែ timing ឱ្យវែងជាងមុនបន្តិច។",
        "empty_subtitle": "❌ មាន subtitle ខ្លះគ្មានអក្សរ។ សូមកែ SRT ហើយផ្ញើម្តងទៀត។",
        "subtitle_too_long": f"❌ អក្សរ subtitle វែងពេក។ សូមកុំឱ្យលើស {settings.max_subtitle_chars} តួអក្សរក្នុងមួយប្លុក។",
        "srt_timing_exceeds_video": "❌ Timing នៅក្នុង SRT លើសរយៈពេលវីដេអូ។ សូមពិនិត្យម្តងទៀត។",
    }
    return messages.get(code, "ឯកសារ SRT មិនត្រឹមត្រូវ។ សូមពិនិត្យ timing និង format របស់វា។")

def _confirm_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ ចាប់ផ្តើម Dubbing", callback_data=f"dubbing:confirm:{task_id}")],
            [InlineKeyboardButton("🔁 ផ្លាស់ប្តូរ SRT", callback_data=f"dubbing:change_srt:{task_id}")],
            [InlineKeyboardButton("❌ បោះបង់", callback_data=f"dubbing:cancel:{task_id}")],
        ]
    )

def retry_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown when a failed task can be resumed."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 ព្យាយាមម្តងទៀត", callback_data=f"dubbing:retry:{task_id}")],
            [InlineKeyboardButton("🎬 ចាប់ផ្តើមថ្មី", callback_data="start_dubbing")],
        ]
    )

def _format_srt_preview(items: list[SubtitleItem], video_duration: float, voice: str, queue_count: int, fix_report: FixReport | None = None) -> str:
    subtitle_count = len(items)
    last_end = max((item.end for item in items), default=0.0)
    total_chars = sum(len(item.text) for item in items)
    estimated_position = queue_count + 1
    voice_label = VOICE_LABELS.get(voice, voice)
    runtime = runtime_settings.cached()
    estimate = estimate_processing_time(
        video_duration=video_duration,
        subtitle_count=subtitle_count,
        total_chars=total_chars,
        queue_count=queue_count,
        provider=str(runtime.get("tts_provider", settings.tts_provider)),
    )
    estimate_text = format_processing_estimate(estimate) if bool(runtime.get("show_processing_estimate", True)) else ""

    sample_lines: list[str] = []
    for item in items[:3]:
        speaker = f"[{item.character_label}] " if getattr(item, "character_label", None) else ""
        sample_lines.append(f"{item.index}. {speaker}{truncate(item.text, 72)}")
    sample = "\n".join(sample_lines) if sample_lines else "គ្មាន preview"

    character_lines = summarize_character_voices(items, voice) if bool(runtime.get("multi_voice_enabled", True)) else []
    character_text = "\n".join(character_lines) if character_lines else "• មិនមាន label តួអង្គ — ប្រើសម្លេងដែលបានជ្រើសសម្រាប់គ្រប់បន្ទាត់"

    timing_ok = "ត្រឹមត្រូវ ✅" if last_end <= video_duration + 0.2 else "លើសវីដេអូ ⚠️"
    estimate_block = f"\n⏱ ពេលវេលាប៉ាន់ស្មាន\n{estimate_text}\n" if estimate_text else ""
    fixer_block = ""
    if fix_report is not None:
        fixer_title = "🛠 Auto Subtitle Fixer"
        fixer_body = format_fix_report_khmer(fix_report)
        fixer_block = f"\n{fixer_title}\n{fixer_body}\n"
    return (
        f"{step_title(4, 4, 'ពិនិត្យ Subtitle Preview')}\n\n"
        "សូមពិនិត្យព័ត៌មានខាងក្រោម មុនចាប់ផ្តើមដំណើរការ។\n\n"
        "📋 ព័ត៌មានសង្ខេប\n"
        f"• Subtitle: {subtitle_count} ប្លុក\n"
        f"• Timing ចុងក្រោយ: {seconds_to_readable(last_end)} ({timing_ok})\n"
        f"• រយៈពេលវីដេអូ: {seconds_to_readable(video_duration)}\n"
        f"• សម្លេង Default: {voice_label}\n"
        f"• តួអក្សរសរុប: {total_chars}\n"
        f"• Queue រំពឹងទុក: ជួរទី {estimated_position}\n"
        f"{estimate_block}"
        f"{fixer_block}\n"
        "👥 Multi Voice Per Character\n"
        f"{character_text}\n\n"
        "🔎 Preview 3 បន្ទាត់ដំបូង\n"
        f"{sample}\n\n"
        "បើគ្រប់យ៉ាងត្រឹមត្រូវ សូមចុច ✅ ចាប់ផ្តើម Dubbing។"
    )

def _queue_text(position: int | None, estimate_text: str | None = None) -> str:
    estimate_block = f"\n\n⏱ {estimate_text}" if estimate_text else ""
    if position and position > 1:
        return (
            "✅ បានដាក់ចូល Queue រួចហើយ\n\n"
            f"⏳ ជួររបស់អ្នក: លេខ {position}\n"
            "ខ្ញុំនឹងដំណើរការដោយស្វ័យប្រវត្តិ ពេលដល់វេនរបស់អ្នក។"
            f"{estimate_block}\n\n"
            "ប្រើ /status ដើម្បីមើលស្ថានភាព។"
        )
    return (
        "⚙️ កំពុងចាប់ផ្តើម AI Dubbing...\n\n"
        f"{percent_line(12)}\n"
        "សូមរង់ចាំបន្តិច 🙏"
        f"{estimate_block}"
    )

def _user_can_access_task(task: dict[str, Any] | None, telegram_user_id: int) -> bool:
    if not task:
        return False
    if int(task.get("telegram_user_id") or 0) == int(telegram_user_id):
        return True
    return telegram_user_id in settings.admin_ids


def _already_started_text(status: str, progress: str | int = 0, queue_position: int | None = None) -> str:
    position = f"\n⏳ Queue: ជួរទី {queue_position}" if queue_position else ""
    return (
        "ℹ️ Task នេះបានចាប់ផ្តើមរួចហើយ។\n\n"
        f"📌 Status: {status_label(status)}\n"
        f"📊 Progress: {percent_line(progress)}"
        f"{position}\n\n"
        "ប្រើ /status ដើម្បីមើលស្ថានភាពចុងក្រោយ។"
    )


async def _enqueue_task(
    *,
    task_id: str,
    telegram_user_id: int,
    chat_id: int,
    voice: str,
    video_path: str,
    srt_path: str,
    video_duration: float,
    progress_message_id: int | None,
    estimated_seconds: int | None = None,
) -> int:
    """Queue a task and return its 1-based queue position at enqueue time."""
    payload = {
        "task_id": task_id,
        "telegram_user_id": telegram_user_id,
        "chat_id": chat_id,
        "voice": voice,
        "video_path": video_path,
        "srt_path": srt_path,
        "video_duration": video_duration,
        "progress_message_id": progress_message_id,
        "estimated_seconds": estimated_seconds or 0,
    }
    position = await redis_service.enqueue(payload)
    await redis_service.set_task_meta(
        task_id,
        {
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "voice": voice,
            "video_path": video_path,
            "srt_path": srt_path,
            "video_duration": video_duration,
            "progress_message_id": progress_message_id or "",
            "last_queue_position": position,
            "estimated_seconds": estimated_seconds or 0,
        },
    )
    return position


async def handle_video_or_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    state = await redis_service.get_user_state(user.id)
    if state == STATE_WAITING_VIDEO:
        await _handle_video(update, context)
        return
    if state in {STATE_WAITING_SRT, STATE_WAITING_CONFIRM}:
        # When the user sends a new SRT while a preview is open, replace the previous SRT.
        await _handle_srt(update, context)
        return
    if state == STATE_PROCESSING:
        await message.reply_text("⚙️ Task របស់អ្នកកំពុងដំណើរការ។ ប្រើ /status ដើម្បីមើលស្ថានភាព ឬ /cancel ដើម្បីបោះបង់។")
        return

    await message.reply_text("សូមចុច /start ដើម្បីចាប់ផ្តើម ឬ /help ដើម្បីមើលរបៀបប្រើ។")


async def _handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    assert user and message

    voice = await redis_service.get_user_voice(user.id)
    if not voice:
        await message.reply_text("សូមជ្រើសសម្លេង AI ជាមុនសិន។ ចុច /start ដើម្បីចាប់ផ្តើម។")
        return

    progress_msg = await message.reply_text(f"{step_title(3, 4, 'កំពុងទទួលវីដេអូ')}\n\n{percent_line(10)}\nកំពុងពិនិត្យវីដេអូ...")
    video_path: str | None = None
    try:
        user_row = await supabase_service.upsert_user(user, selected_voice=voice)
        video = await download_and_validate_video(message, context.bot)
        video_path = video["path"]
        task_id = str(uuid.uuid4())
        task_payload = {
            "id": task_id,
            "user_id": user_row.get("id") if user_row else None,
            "telegram_user_id": user.id,
            "status": TASK_WAITING_SRT,
            "voice": voice,
            "video_file_id": video["file_id"],
            "video_file_path": video["path"],
            "video_duration": video["duration"],
            "file_size": video["file_size"],
            "progress": 10,
        }
        await supabase_service.create_task(task_payload)
        await redis_service.set_user_task(user.id, task_id)
        await redis_service.set_user_state(user.id, STATE_WAITING_SRT)
        await redis_service.set_task_meta(
            task_id,
            {
                "telegram_user_id": user.id,
                "chat_id": message.chat_id,
                "voice": voice,
                "video_path": video["path"],
                "video_duration": video["duration"],
                "progress_message_id": progress_msg.message_id,
            },
        )
        await redis_service.set_task_status(task_id, TASK_WAITING_SRT, 10)
        await progress_msg.edit_text(
            _format_srt_instruction_message(),
            reply_markup=_srt_instruction_keyboard(task_id),
            disable_web_page_preview=True,
            parse_mode="HTML",
        )
    except ValueError as exc:
        delete_file(video_path)
        await progress_msg.edit_text(_khmer_video_error(str(exc)))
    except Exception as exc:
        logger.exception("Video handling failed: %s", exc)
        delete_file(video_path)
        await log_db("error", "video_upload", "Video upload failed", {"user": user.id, "error": str(exc)})
        await progress_msg.edit_text("សូមទោស មិនអាចទាញយកវីដេអូបានទេ។ សូមព្យាយាមផ្ញើម្តងទៀត។")


async def _handle_srt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    assert user and message

    task_id = await redis_service.get_user_task(user.id)
    if not task_id:
        await message.reply_text("រកមិនឃើញ Task របស់អ្នកទេ។ សូមចុច /start ដើម្បីចាប់ផ្តើមថ្មី។")
        return

    meta = await redis_service.get_task_meta(task_id)
    video_path = meta.get("video_path")
    if not video_path or not Path(str(video_path)).exists():
        await update_task_status(
            task_id,
            TASK_CANCELLED,
            progress=0,
            error_message="Video temp file missing before SRT upload; likely stale task after restart/redeploy.",
            mark_finished=True,
        )
        await redis_service.set_user_state(user.id, STATE_IDLE)
        await redis_service.delete(f"user:{user.id}:task")
        await message.reply_text(
            "Task ចាស់នេះរកវីដេអូមិនឃើញទេ។ សូមចុច /start ហើយផ្ញើវីដេអូថ្មីម្តងទៀត។"
        )
        return

    # Replace a previous SRT preview if the user uploads another SRT before confirming.
    old_srt_path = meta.get("srt_path")
    if old_srt_path:
        delete_file(str(old_srt_path))

    video_duration = float(meta.get("video_duration", "0") or 0)
    srt_path: str | None = None
    try:
        srt = await download_srt(message, context.bot, task_id)
        srt_path = srt["path"]
        runtime = await runtime_settings.load()
        fix_report: FixReport | None = None

        if bool(runtime.get("auto_srt_fixer_enabled", True)):
            try:
                fix_report = fix_srt_file(
                    Path(srt_path),
                    video_duration=video_duration,
                    max_overlap_seconds=float(runtime.get("auto_srt_fixer_max_overlap_seconds", 1.2)),
                    max_video_overrun_seconds=float(runtime.get("auto_srt_fixer_max_video_overrun_seconds", 2.0)),
                    min_gap_seconds=float(runtime.get("auto_srt_fixer_min_gap_ms", 50)) / 1000.0,
                )
            except ValueError:
                # Let the normal validator produce the user-facing error code.
                fix_report = None

        items = validate_srt_file(Path(srt_path), video_duration)
        queue_count = await redis_service.queue_count()
        voice = meta.get("voice") or await redis_service.get_user_voice(user.id) or ""

        await supabase_service.update_task(
            task_id,
            {
                "status": TASK_WAITING_SRT,
                "srt_file_id": srt["file_id"],
                "srt_file_path": srt_path,
                "progress": 12,
                "error_message": None,
            },
        )
        await redis_service.set_task_meta(
            task_id,
            {
                "srt_path": srt_path,
                "srt_file_id": srt["file_id"],
                "subtitle_count": len(items),
                "subtitle_last_end": max(item.end for item in items),
                "subtitle_chars": sum(len(item.text) for item in items),
                "detected_characters": ", ".join(sorted({str(item.character_label) for item in items if item.character_label})),
                "auto_srt_fixed": "true" if fix_report and fix_report.fixed else "false",
                "auto_srt_fixes": "; ".join((fix_report.fixes if fix_report else [])[:8]),
            },
        )
        await redis_service.set_task_status(task_id, TASK_WAITING_SRT, 12)
        await redis_service.set_user_state(user.id, STATE_WAITING_CONFIRM)

        await message.reply_text(
            _format_srt_preview(items, video_duration, voice, queue_count, fix_report),
            reply_markup=_confirm_keyboard(task_id),
        )
    except ValueError as exc:
        delete_file(srt_path)
        await message.reply_text(_khmer_srt_error(str(exc)))
    except Exception as exc:
        logger.exception("SRT handling failed: %s", exc)
        delete_file(srt_path)
        await log_db("error", "srt_upload", "SRT upload failed", {"user": user.id, "task_id": task_id, "error": str(exc)})
        await message.reply_text("សូមទោស មិនអាចទទួលឯកសារ SRT បានទេ។ សូមព្យាយាមផ្ញើម្តងទៀត។")


async def dubbing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user callbacks for preview confirmation, SRT replacement, cancel, and retry."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":", 2)
    if len(parts) < 3:
        await query.edit_message_text("សំណើនេះមិនត្រឹមត្រូវទេ។ សូមចុច /start ម្តងទៀត។")
        return

    action, task_id = parts[1], parts[2]
    if action == "confirm":
        await _confirm_task(query, user.id)
    elif action == "change_srt":
        await _change_srt(query, user.id, task_id)
    elif action == "cancel":
        await _cancel_task_from_button(query, user.id, task_id)
    elif action == "retry":
        await _retry_failed_task(query, user.id, task_id)
    else:
        await query.edit_message_text("Action នេះមិនត្រឹមត្រូវទេ។ សូមចុច /start ម្តងទៀត។")


async def _confirm_task(query, telegram_user_id: int) -> None:
    task_id = (query.data or "").split(":", 2)[2]
    owner = f"confirm:{telegram_user_id}:{query.id}"
    if not await redis_service.acquire_enqueue_lock(task_id, owner, ttl_seconds=30):
        redis_status = await redis_service.get_task_status(task_id)
        queue_position = await redis_service.queue_position(task_id)
        await query.edit_message_text(
            _already_started_text(redis_status.get("status", TASK_QUEUED), redis_status.get("progress", 12), queue_position)
        )
        return

    try:
        task = await supabase_service.get_task(task_id)
        if not _user_can_access_task(task, telegram_user_id):
            await query.edit_message_text("អ្នកមិនមានសិទ្ធិដំណើរការ Task នេះទេ។")
            return

        # Re-check after the lock is acquired. This closes the race where two
        # callback updates both read waiting_srt before either one enqueues.
        task_status = str((task or {}).get("status") or "")
        redis_status = await redis_service.get_task_status(task_id)
        effective_status = redis_status.get("status") or task_status
        if effective_status in {TASK_QUEUED, TASK_PROCESSING, TASK_COMPLETED}:
            queue_position = await redis_service.queue_position(task_id)
            progress = redis_status.get("progress") or (task or {}).get("progress") or 0
            await query.edit_message_text(_already_started_text(effective_status, progress, queue_position))
            return

        meta = await redis_service.get_task_meta(task_id)
        video_path = meta.get("video_path") or (task or {}).get("video_file_path")
        srt_path = meta.get("srt_path") or (task or {}).get("srt_file_path")
        voice = meta.get("voice") or (task or {}).get("voice") or await redis_service.get_user_voice(telegram_user_id) or ""
        video_duration = float(meta.get("video_duration") or (task or {}).get("video_duration") or 0)

        if not video_path or not srt_path or not Path(str(video_path)).exists() or not Path(str(srt_path)).exists():
            await update_task_status(
                task_id,
                TASK_FAILED,
                progress=0,
                error_message="Task files missing before confirmation; likely Render temp cleanup or redeploy.",
                mark_finished=True,
            )
            await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
            await redis_service.delete(f"user:{telegram_user_id}:task")
            await query.edit_message_text(
                "សូមទោស រកឯកសារ Video/SRT មិនឃើញទេ។ សូមចុច /start ដើម្បីចាប់ផ្តើមថ្មី។"
            )
            return

        subtitle_count = int(meta.get("subtitle_count") or 0)
        subtitle_chars = int(meta.get("subtitle_chars") or 0)
        runtime = await runtime_settings.load()
        estimate = estimate_processing_time(
            video_duration=video_duration,
            subtitle_count=subtitle_count,
            total_chars=subtitle_chars,
            queue_count=await redis_service.queue_count(),
            provider=str(runtime.get("tts_provider", settings.tts_provider)),
        )
        estimate_text = format_processing_estimate(estimate) if bool(runtime.get("show_processing_estimate", True)) else None

        await supabase_service.update_task(
            task_id,
            {
                "status": TASK_QUEUED,
                "progress": 12,
                "error_message": None,
            },
        )
        await redis_service.set_task_status(task_id, TASK_QUEUED, 12)
        await redis_service.set_user_task(telegram_user_id, task_id)
        await redis_service.set_user_state(telegram_user_id, STATE_PROCESSING)

        position = await _enqueue_task(
            task_id=task_id,
            telegram_user_id=telegram_user_id,
            chat_id=query.message.chat_id,
            voice=voice,
            video_path=str(video_path),
            srt_path=str(srt_path),
            video_duration=video_duration,
            progress_message_id=query.message.message_id,
            estimated_seconds=estimate.total_seconds,
        )
        await query.edit_message_text(_queue_text(position, estimate_text))
    finally:
        await redis_service.release_enqueue_lock(task_id, owner)

async def _change_srt(query, telegram_user_id: int, task_id: str) -> None:
    current_task = await redis_service.get_user_task(telegram_user_id)
    task = await supabase_service.get_task(task_id)
    if current_task != task_id and not _user_can_access_task(task, telegram_user_id):
        await query.edit_message_text("អ្នកមិនមានសិទ្ធិកែ Task នេះទេ។")
        return

    meta = await redis_service.get_task_meta(task_id)
    old_srt = meta.get("srt_path") or (task or {}).get("srt_file_path")
    if old_srt:
        delete_file(str(old_srt))
    await redis_service.set_task_meta(task_id, {"srt_path": "", "srt_file_id": ""})
    await redis_service.set_task_status(task_id, TASK_WAITING_SRT, 10)
    await redis_service.set_user_task(telegram_user_id, task_id)
    await redis_service.set_user_state(telegram_user_id, STATE_WAITING_SRT)
    await supabase_service.update_task(
        task_id,
        {
            "status": TASK_WAITING_SRT,
            "progress": 10,
            "srt_file_id": None,
            "srt_file_path": None,
            "error_message": None,
        },
    )
    await query.edit_message_text("🔁 សូមផ្ញើឯកសារ SRT ថ្មីសម្រាប់វីដេអូនេះ។")


async def _cancel_task_from_button(query, telegram_user_id: int, task_id: str) -> None:
    current_task = await redis_service.get_user_task(telegram_user_id)
    task = await supabase_service.get_task(task_id)
    if current_task != task_id and not _user_can_access_task(task, telegram_user_id):
        await query.edit_message_text("អ្នកមិនមានសិទ្ធិបោះបង់ Task នេះទេ។")
        return

    meta = await redis_service.get_task_meta(task_id)
    for path in (meta.get("srt_path"),):
        if path:
            delete_file(str(path))
    await redis_service.remove_task_from_queue(task_id)
    await update_task_status(task_id, TASK_CANCELLED, progress=0, error_message="Cancelled by user before processing", mark_finished=True)
    await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
    await redis_service.delete(f"user:{telegram_user_id}:task")
    await query.edit_message_text("✅ បានបោះបង់ Task រួចហើយ។ ចុច /start ដើម្បីចាប់ផ្តើមថ្មី។")


async def _retry_failed_task(query, telegram_user_id: int, task_id: str) -> None:
    owner = f"retry:{telegram_user_id}:{query.id}"
    if not await redis_service.acquire_enqueue_lock(task_id, owner, ttl_seconds=30):
        redis_status = await redis_service.get_task_status(task_id)
        queue_position = await redis_service.queue_position(task_id)
        await query.edit_message_text(
            _already_started_text(redis_status.get("status", TASK_QUEUED), redis_status.get("progress", 12), queue_position)
        )
        return

    try:
        task = await supabase_service.get_task(task_id)
        if not _user_can_access_task(task, telegram_user_id):
            await query.edit_message_text("អ្នកមិនមានសិទ្ធិ Retry Task នេះទេ។")
            return

        redis_status = await redis_service.get_task_status(task_id)
        effective_status = redis_status.get("status") or (task or {}).get("status")
        if effective_status in {TASK_QUEUED, TASK_PROCESSING}:
            queue_position = await redis_service.queue_position(task_id)
            await query.edit_message_text(_already_started_text(effective_status, redis_status.get("progress", 12), queue_position))
            return
        if (task or {}).get("status") not in {TASK_FAILED, TASK_CANCELLED}:
            await query.edit_message_text("Task នេះមិនមែនជា Failed Task ទេ។ ប្រើ /status ដើម្បីមើលស្ថានភាព។")
            return

        video_path = str((task or {}).get("video_file_path") or "")
        srt_path = str((task or {}).get("srt_file_path") or "")
        if not video_path or not srt_path or not Path(video_path).exists() or not Path(srt_path).exists():
            await query.edit_message_text(
                "មិនអាចព្យាយាមម្តងទៀតបានទេ ព្រោះឯកសារ video/SRT មិនមាននៅលើ server ទៀតហើយ។\n\n"
                "សូមចុច /start ហើយផ្ញើវីដេអូ + SRT ម្តងទៀត។"
            )
            return

        voice = str((task or {}).get("voice") or await redis_service.get_user_voice(telegram_user_id) or "")
        video_duration = float((task or {}).get("video_duration") or 0)
        await redis_service.remove_task_from_queue(task_id)
        await supabase_service.update_task(
            task_id,
            {
                "status": TASK_QUEUED,
                "progress": 12,
                "error_message": None,
                "output_file_path": None,
                "started_at": None,
                "completed_at": None,
            },
        )
        await redis_service.set_task_status(task_id, TASK_QUEUED, 12)
        await redis_service.set_user_task(telegram_user_id, task_id)
        await redis_service.set_user_state(telegram_user_id, STATE_PROCESSING)

        meta = await redis_service.get_task_meta(task_id)
        subtitle_count = int(meta.get("subtitle_count") or 0)
        subtitle_chars = int(meta.get("subtitle_chars") or 0)
        runtime = await runtime_settings.load()
        estimate = estimate_processing_time(
            video_duration=video_duration,
            subtitle_count=subtitle_count,
            total_chars=subtitle_chars,
            queue_count=await redis_service.queue_count(),
            provider=str(runtime.get("tts_provider", settings.tts_provider)),
        )
        estimate_text = format_processing_estimate(estimate) if bool(runtime.get("show_processing_estimate", True)) else None
        position = await _enqueue_task(
            task_id=task_id,
            telegram_user_id=telegram_user_id,
            chat_id=query.message.chat_id,
            voice=voice,
            video_path=video_path,
            srt_path=srt_path,
            video_duration=video_duration,
            progress_message_id=query.message.message_id,
            estimated_seconds=estimate.total_seconds,
        )
        await query.edit_message_text(_queue_text(position, estimate_text))
    finally:
        await redis_service.release_enqueue_lock(task_id, owner)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    task_id = await redis_service.get_user_task(user.id)
    if not task_id:
        await message.reply_text(
            "ℹ️ អ្នកមិនមាន Task កំពុងដំណើរការទេ។\n\n"
            "ចុច /start ដើម្បីចាប់ផ្តើម Dubbing ថ្មី។"
        )
        return
    status = await redis_service.get_task_status(task_id)
    if not status:
        task = await supabase_service.get_task(task_id)
        status = {"status": task.get("status", "unknown"), "progress": str(task.get("progress", 0))} if task else {}

    raw_status = status.get("status", "unknown")
    progress = status.get("progress", "0")
    queue_position = await redis_service.queue_position(task_id)
    position_line = f"\n⏳ Queue: ជួរទី {queue_position}" if queue_position else ""
    meta = await redis_service.get_task_meta(task_id)
    estimate_seconds = int(meta.get("estimated_seconds") or 0)
    estimate_line = ""
    if estimate_seconds:
        estimate_line = "\n⏱ " + format_processing_estimate(ProcessingEstimate(processing_seconds=estimate_seconds, queue_wait_seconds=0))
    await message.reply_text(
        f"{status_emoji(raw_status)} ស្ថានភាព Task\n\n"
        f"🆔 ID: {task_id[:8]}\n"
        f"📌 Status: {status_label(raw_status)}\n"
        f"📊 Progress: {percent_line(progress)}"
        f"{position_line}"
        f"{estimate_line}\n\n"
        "ប្រើ /cancel ប្រសិនបើចង់បោះបង់ Task នេះ។"
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    task_id = await redis_service.get_user_task(user.id)
    if not task_id:
        await message.reply_text("អ្នកមិនមាន Task សម្រាប់បោះបង់ទេ។")
        return
    await redis_service.remove_task_from_queue(task_id)
    await update_task_status(task_id, TASK_CANCELLED, progress=0, error_message="Cancelled by user", mark_finished=True)
    await redis_service.set_user_state(user.id, STATE_IDLE)
    await redis_service.delete(f"user:{user.id}:task")
    await message.reply_text("✅ បានបោះបង់ Task រួចហើយ។ ចុច /start ដើម្បីចាប់ផ្តើមថ្មី។")
