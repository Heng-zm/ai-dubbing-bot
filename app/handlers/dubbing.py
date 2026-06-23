"""User dubbing flow handlers."""

from __future__ import annotations

import uuid
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from app.config import settings
from app.services.logger_service import log_db, logger
from app.services.redis_service import redis_service
from app.services.srt_parser import validate_srt_file
from app.services.supabase_service import supabase_service
from app.services.task_service import update_task_status
from app.services.telegram_files import download_and_validate_video, download_srt
from app.states import (
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_WAITING_SRT,
    STATE_WAITING_VIDEO,
    TASK_CANCELLED,
    TASK_QUEUED,
    TASK_WAITING_SRT,
)
from app.utils.file_utils import delete_file


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


async def handle_video_or_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    state = await redis_service.get_user_state(user.id)
    if state == STATE_WAITING_VIDEO:
        await _handle_video(update, context)
        return
    if state == STATE_WAITING_SRT:
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
    video_duration = float(meta.get("video_duration", "0") or 0)
    srt_path: str | None = None
    try:
        srt = await download_srt(message, context.bot, task_id)
        srt_path = srt["path"]
        validate_srt_file(Path(srt_path), video_duration)

        await supabase_service.update_task(
            task_id,
            {
                "status": TASK_QUEUED,
                "srt_file_id": srt["file_id"],
                "srt_file_path": srt_path,
                "progress": 12,
            },
        )
        await redis_service.set_task_meta(task_id, {"srt_path": srt_path})
        await redis_service.set_task_status(task_id, TASK_QUEUED, 12)
        await redis_service.set_user_state(user.id, STATE_PROCESSING)

        progress = await message.reply_text("កំពុងដំណើរការ AI Dubbing... សូមរង់ចាំបន្តិច 🙏")
        await redis_service.set_task_meta(task_id, {"progress_message_id": progress.message_id, "chat_id": message.chat_id})
        await redis_service.enqueue(
            {
                "task_id": task_id,
                "telegram_user_id": user.id,
                "chat_id": message.chat_id,
                "voice": meta.get("voice"),
                "video_path": meta.get("video_path"),
                "srt_path": srt_path,
                "video_duration": video_duration,
                "progress_message_id": progress.message_id,
            }
        )
    except ValueError as exc:
        delete_file(srt_path)
        await message.reply_text(_khmer_srt_error(str(exc)))
    except Exception as exc:
        logger.exception("SRT handling failed: %s", exc)
        delete_file(srt_path)
        await log_db("error", "srt_upload", "SRT upload failed", {"user": user.id, "task_id": task_id, "error": str(exc)})
        await message.reply_text("សូមទោស មិនអាចទទួលឯកសារ SRT បានទេ។ សូមព្យាយាមម្តងទៀត។")


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
    await message.reply_text(
        f"ស្ថានភាព Task:\n• ID: {task_id[:8]}\n• Status: {status.get('status', 'unknown')}\n• Progress: {status.get('progress', '0')}%"
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
