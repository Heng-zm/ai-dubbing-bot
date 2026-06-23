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
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config import settings
from app.services.logger_service import log_db, logger
from app.services.redis_service import redis_service
from app.services.srt_parser import SubtitleItem, validate_srt_file
from app.services.supabase_service import supabase_service
from app.services.task_service import update_task_status
from app.services.telegram_files import download_and_validate_video, download_srt
from app.states import (
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_WAITING_CONFIRM,
    STATE_WAITING_SRT,
    STATE_WAITING_VIDEO,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_PROCESSING,
    TASK_QUEUED,
    TASK_WAITING_SRT,
    VOICE_LABELS,
)
from app.utils.file_utils import delete_file
from app.utils.text_utils import truncate
from app.utils.time_utils import seconds_to_readable


def _khmer_video_error(code: str) -> str:
    messages = {
        "unsupported_video_format": "សូមផ្ញើវីដេអូជា format mp4, mov, mkv ឬ webm ប៉ុណ្ណោះ។",
        "video_too_large": f"វីដេអូធំពេក។ សូមផ្ញើឯកសារមិនលើស {settings.max_video_size_mb}MB។",
        "video_too_long": "វីដេអូវែងពេក។ សូមផ្ញើវីដេអូដែលមានរយៈពេលមិនលើសពី 1 នាទី។",
        "no_video": "សូមផ្ញើវីដេអូ ឬឯកសារវីដេអូ។",
    }
    return messages.get(code, "សូមទោស មិនអាចទទួលវីដេអូនេះបានទេ។ សូមព្យាយាមម្តងទៀត។")


def _khmer_srt_error(code: str) -> str:
    messages = {
        "no_srt": "សូមផ្ញើឯកសារ SRT ជា Document។",
        "not_srt": "សូមផ្ញើឯកសារ .srt ប៉ុណ្ណោះ។",
        "srt_too_large": f"ឯកសារ SRT ធំពេក។ សូមផ្ញើឯកសារមិនលើស {settings.max_srt_size_mb}MB។",
        "invalid_srt": "ឯកសារ SRT មិនត្រឹមត្រូវ។ សូមពិនិត្យ format របស់វា។",
        "invalid_srt_timing": "Timing នៅក្នុង SRT មិនត្រឹមត្រូវ។",
        "subtitle_overlap": "Timing subtitle មានការជាន់គ្នា។ សូមកែ SRT ហើយផ្ញើម្តងទៀត។",
        "subtitle_too_short": "Subtitle ខ្លីពេក។ សូមកែ timing ឱ្យបានត្រឹមត្រូវ។",
        "empty_subtitle": "មាន subtitle ខ្លះគ្មានអក្សរ។ សូមកែ SRT ហើយផ្ញើម្តងទៀត។",
        "subtitle_too_long": f"អក្សរ subtitle វែងពេក។ សូមកុំឱ្យលើស {settings.max_subtitle_chars} តួអក្សរក្នុងមួយបន្ទាត់។",
        "srt_timing_exceeds_video": "Timing នៅក្នុង SRT លើសរយៈពេលវីដេអូ។ សូមពិនិត្យម្តងទៀត។",
    }
    return messages.get(code, "ឯកសារ SRT មិនត្រឹមត្រូវ។ សូមពិនិត្យ timing និង format របស់វា។")


def _confirm_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ចាប់ផ្តើម Dubbing ✅", callback_data=f"dubbing:confirm:{task_id}")],
            [InlineKeyboardButton("ផ្លាស់ប្តូរ SRT 🔁", callback_data=f"dubbing:change_srt:{task_id}")],
            [InlineKeyboardButton("បោះបង់ ❌", callback_data=f"dubbing:cancel:{task_id}")],
        ]
    )


def retry_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown when a failed task can be resumed."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ព្យាយាមម្តងទៀត 🔄", callback_data=f"dubbing:retry:{task_id}")],
            [InlineKeyboardButton("ចាប់ផ្តើមថ្មី 🎬", callback_data="start_dubbing")],
        ]
    )


def _format_srt_preview(items: list[SubtitleItem], video_duration: float, voice: str, queue_count: int) -> str:
    subtitle_count = len(items)
    last_end = max((item.end for item in items), default=0.0)
    total_chars = sum(len(item.text) for item in items)
    estimated_position = queue_count + 1
    voice_label = VOICE_LABELS.get(voice, voice)

    sample_lines: list[str] = []
    for item in items[:3]:
        sample_lines.append(f"{item.index}. {truncate(item.text, 70)}")
    sample = "\n".join(sample_lines) if sample_lines else "គ្មាន preview"

    return (
        "សូមពិនិត្យ SRT មុនពេលដំណើរការ 🎙️\n\n"
        f"• ចំនួន Subtitle: {subtitle_count}\n"
        f"• Timing ចុងក្រោយ: {seconds_to_readable(last_end)}\n"
        f"• រយៈពេលវីដេអូ: {seconds_to_readable(video_duration)}\n"
        f"• សម្លេង: {voice_label}\n"
        f"• តួអក្សរសរុប: {total_chars}\n"
        f"• ជួរដែលរំពឹងទុក: លេខ {estimated_position}\n\n"
        "Preview:\n"
        f"{sample}\n\n"
        "បើត្រឹមត្រូវ សូមចុច ចាប់ផ្តើម Dubbing ✅"
    )


def _queue_text(position: int | None) -> str:
    if position and position > 1:
        return (
            "Task របស់អ្នកត្រូវបានដាក់ចូល Queue ហើយ ✅\n\n"
            f"ការងាររបស់អ្នកស្ថិតនៅជួរទី {position}។\n"
            "កំពុងរង់ចាំដំណើរការ..."
        )
    return "កំពុងដំណើរការ AI Dubbing... សូមរង់ចាំបន្តិច 🙏"


def _user_can_access_task(task: dict[str, Any] | None, telegram_user_id: int) -> bool:
    if not task:
        return False
    if int(task.get("telegram_user_id") or 0) == int(telegram_user_id):
        return True
    return telegram_user_id in settings.admin_ids


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
        await message.reply_text("Task របស់អ្នកកំពុងដំណើរការ។ សូមរង់ចាំ ឬប្រើ /status ដើម្បីមើលស្ថានភាព។")
        return

    await message.reply_text("សូមចុច /start រួចចុចប៊ូតុង សម្រាយរឿង ដើម្បីចាប់ផ្តើម។")


async def _handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    assert user and message

    voice = await redis_service.get_user_voice(user.id)
    if not voice:
        await message.reply_text("សូមជ្រើសរើសសម្លេង AI ជាមុនសិន។ ចុច /start ដើម្បីចាប់ផ្តើម។")
        return

    progress_msg = await message.reply_text("កំពុងរៀបចំឯកសារ... 10%")
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
        await progress_msg.edit_text("វីដេអូបានទទួលហើយ ✅\n\nសូមផ្ញើឯកសារ SRT សម្រាប់វីដេអូនេះ។")
    except ValueError as exc:
        delete_file(video_path)
        await progress_msg.edit_text(_khmer_video_error(str(exc)))
    except Exception as exc:
        logger.exception("Video handling failed: %s", exc)
        delete_file(video_path)
        await log_db("error", "video_upload", "Video upload failed", {"user": user.id, "error": str(exc)})
        await progress_msg.edit_text("សូមទោស មានបញ្ហាក្នុងការទាញយកវីដេអូ។ សូមព្យាយាមម្តងទៀត។")


async def _handle_srt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    assert user and message

    task_id = await redis_service.get_user_task(user.id)
    if not task_id:
        await message.reply_text("រកមិនឃើញ Task របស់អ្នកទេ។ សូមចាប់ផ្តើមម្តងទៀតដោយចុច /start។")
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
            "Task ចាស់នេះរកវីដេអូមិនឃើញទេ។ សូមចាប់ផ្តើមម្តងទៀតដោយចុច /start ហើយផ្ញើវីដេអូថ្មី។"
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
            },
        )
        await redis_service.set_task_status(task_id, TASK_WAITING_SRT, 12)
        await redis_service.set_user_state(user.id, STATE_WAITING_CONFIRM)

        await message.reply_text(
            _format_srt_preview(items, video_duration, voice, queue_count),
            reply_markup=_confirm_keyboard(task_id),
        )
    except ValueError as exc:
        delete_file(srt_path)
        await message.reply_text(_khmer_srt_error(str(exc)))
    except Exception as exc:
        logger.exception("SRT handling failed: %s", exc)
        delete_file(srt_path)
        await log_db("error", "srt_upload", "SRT upload failed", {"user": user.id, "task_id": task_id, "error": str(exc)})
        await message.reply_text("សូមទោស មិនអាចទទួលឯកសារ SRT បានទេ។ សូមព្យាយាមម្តងទៀត។")


async def dubbing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user callbacks for preview confirmation, SRT replacement, cancel, and retry."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":", 2)
    if len(parts) < 3:
        await query.edit_message_text("Callback មិនត្រឹមត្រូវ។ សូមចុច /start ម្តងទៀត។")
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
        await query.edit_message_text("Action មិនត្រឹមត្រូវ។ សូមចុច /start ម្តងទៀត។")


async def _confirm_task(query, telegram_user_id: int) -> None:
    task_id = (query.data or "").split(":", 2)[2]
    task = await supabase_service.get_task(task_id)
    if not _user_can_access_task(task, telegram_user_id):
        await query.edit_message_text("អ្នកមិនមានសិទ្ធិដំណើរការ Task នេះទេ។")
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
            "សូមទោស រកឯកសារ video/SRT មិនឃើញទេ។ សូមចាប់ផ្តើមថ្មីដោយចុច /start។"
        )
        return

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
    )
    await query.edit_message_text(_queue_text(position))


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
    await query.edit_message_text("សូមផ្ញើឯកសារ SRT ថ្មីសម្រាប់វីដេអូនេះ។")


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
    await update_task_status(task_id, TASK_CANCELLED, progress=0, error_message="Cancelled by user before processing", mark_finished=True)
    await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
    await redis_service.delete(f"user:{telegram_user_id}:task")
    await query.edit_message_text("Task ត្រូវបានបោះបង់ហើយ។ ចុច /start ដើម្បីចាប់ផ្តើមម្តងទៀត។")


async def _retry_failed_task(query, telegram_user_id: int, task_id: str) -> None:
    task = await supabase_service.get_task(task_id)
    if not _user_can_access_task(task, telegram_user_id):
        await query.edit_message_text("អ្នកមិនមានសិទ្ធិ Retry Task នេះទេ។")
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

    position = await _enqueue_task(
        task_id=task_id,
        telegram_user_id=telegram_user_id,
        chat_id=query.message.chat_id,
        voice=voice,
        video_path=video_path,
        srt_path=srt_path,
        video_duration=video_duration,
        progress_message_id=query.message.message_id,
    )
    await query.edit_message_text(_queue_text(position))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    task_id = await redis_service.get_user_task(user.id)
    if not task_id:
        await message.reply_text("អ្នកមិនមាន Task កំពុងដំណើរការទេ។ ចុច /start ដើម្បីចាប់ផ្តើម។")
        return
    status = await redis_service.get_task_status(task_id)
    if not status:
        task = await supabase_service.get_task(task_id)
        status = {"status": task.get("status", "unknown"), "progress": str(task.get("progress", 0))} if task else {}

    queue_position = await redis_service.queue_position(task_id)
    position_line = f"\n• Queue position: {queue_position}" if queue_position else ""
    await message.reply_text(
        f"ស្ថានភាព Task:\n"
        f"• ID: {task_id[:8]}\n"
        f"• Status: {status.get('status', 'unknown')}\n"
        f"• Progress: {status.get('progress', '0')}%"
        f"{position_line}"
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
    await update_task_status(task_id, TASK_CANCELLED, progress=0, error_message="Cancelled by user", mark_finished=True)
    await redis_service.set_user_state(user.id, STATE_IDLE)
    await redis_service.delete(f"user:{user.id}:task")
    await message.reply_text("Task ត្រូវបានបោះបង់ហើយ។ ចុច /start ដើម្បីចាប់ផ្តើមម្តងទៀត។")
