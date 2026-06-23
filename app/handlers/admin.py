"""Telegram-only admin dashboard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import settings
from app.services.redis_service import redis_service
from app.services.supabase_service import supabase_service
from app.states import STATE_ADMIN_BROADCAST_TEXT, TASK_COMPLETED, TASK_FAILED, TASK_PROCESSING, TASK_QUEUED
from app.utils.file_utils import clean_temp_older_than
from app.utils.text_utils import truncate


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.admin_ids)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Bot Stats", callback_data="admin:stats")],
            [InlineKeyboardButton("👥 Users", callback_data="admin:users"), InlineKeyboardButton("🎬 Tasks", callback_data="admin:tasks")],
            [InlineKeyboardButton("✅ Completed Tasks", callback_data="admin:completed"), InlineKeyboardButton("❌ Failed Tasks", callback_data="admin:failed")],
            [InlineKeyboardButton("🔄 Running Tasks", callback_data="admin:running")],
            [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin:broadcast")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin:settings"), InlineKeyboardButton("🧹 Clean Temp Files", callback_data="admin:clean")],
            [InlineKeyboardButton("🧾 Clear Queue", callback_data="admin:clear_queue"), InlineKeyboardButton("📝 Recent Logs", callback_data="admin:logs")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id if user else None):
        await update.effective_message.reply_text("អ្នកមិនមានសិទ្ធិប្រើ Admin Dashboard ទេ។")
        return
    await update.effective_message.reply_text("Admin Dashboard", reply_markup=admin_keyboard())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query:
        return
    if not is_admin(user.id if user else None):
        await query.answer("No permission", show_alert=True)
        return
    await query.answer()

    action = query.data.split(":", 1)[1]
    if action == "home":
        await query.edit_message_text("Admin Dashboard", reply_markup=admin_keyboard())
    elif action == "stats":
        await _show_stats(query)
    elif action == "users":
        await _show_users(query)
    elif action == "tasks":
        await _show_tasks(query, None)
    elif action == "completed":
        await _show_tasks(query, TASK_COMPLETED)
    elif action == "failed":
        await _show_tasks(query, TASK_FAILED)
    elif action == "running":
        await _show_running(query)
    elif action == "broadcast":
        await redis_service.set_user_state(user.id, STATE_ADMIN_BROADCAST_TEXT)
        await query.edit_message_text("សូមផ្ញើអត្ថបទដែលអ្នកចង់ Broadcast ទៅអ្នកប្រើទាំងអស់។", reply_markup=back_keyboard())
    elif action == "settings":
        await _show_settings(query)
    elif action == "clean":
        deleted = clean_temp_older_than(hours=settings.cleanup_old_temp_hours)
        await query.edit_message_text(f"បានសម្អាត temp files ចំនួន {deleted} files។", reply_markup=back_keyboard())
    elif action == "clear_queue":
        count = await redis_service.purge_queue()
        await query.edit_message_text(f"បានសម្អាត Redis queue ចំនួន {count} job(s)។", reply_markup=back_keyboard())
    elif action == "logs":
        await _show_logs(query)
    elif action == "broadcast_confirm":
        await _confirm_broadcast(update, context)
    elif action == "broadcast_cancel":
        await redis_service.set_user_state(user.id, "idle")
        await redis_service.delete(f"admin:{user.id}:broadcast_text")
        await query.edit_message_text("Broadcast បានបោះបង់។", reply_markup=back_keyboard())


async def _safe_edit(query, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await query.edit_message_text(truncate(text, 3900), reply_markup=reply_markup)


async def _show_stats(query) -> None:
    redis_ok = await redis_service.ping()
    supabase_ok = await supabase_service.health_check()
    queue_count = await redis_service.queue_count() if redis_ok else 0
    stats = await supabase_service.stats() if supabase_ok else {}
    text = (
        "📊 Bot Stats\n\n"
        f"👥 Total users: {stats.get('total_users', 0)}\n"
        f"🎬 Total videos processed: {stats.get('total_tasks', 0)}\n"
        f"🔄 Running tasks: {stats.get('running', 0)}\n"
        f"⏳ Queued tasks: {stats.get('queued', 0)}\n"
        f"✅ Completed tasks: {stats.get('completed', 0)}\n"
        f"❌ Failed tasks: {stats.get('failed', 0)}\n"
        f"📅 Today’s tasks: {stats.get('today_tasks', 0)}\n"
        f"🎙️ Most used voice: {stats.get('most_used_voice', 'N/A')}\n"
        f"📦 Redis queue count: {queue_count}\n"
        f"🧠 Redis: {'OK' if redis_ok else 'FAILED'}\n"
        f"🗄️ Supabase: {'OK' if supabase_ok else 'FAILED'}"
    )
    await _safe_edit(query, text, back_keyboard())


async def _show_users(query) -> None:
    users = await supabase_service.list_users(limit=15)
    if not users:
        text = "មិនទាន់មានអ្នកប្រើទេ។"
    else:
        lines = ["👥 Recent Users", ""]
        for row in users:
            name = " ".join(filter(None, [row.get("first_name"), row.get("last_name")])) or "Unknown"
            username = f"@{row.get('username')}" if row.get("username") else ""
            lines.append(f"• {name} {username} — {row.get('telegram_user_id')}")
        text = "\n".join(lines)
    await _safe_edit(query, text, back_keyboard())


async def _show_tasks(query, status: str | None) -> None:
    tasks = await supabase_service.list_tasks(status=status, limit=15)
    title = "🎬 Recent Tasks" if not status else f"🎬 Tasks: {status}"
    if not tasks:
        text = f"{title}\n\nមិនមានទិន្នន័យទេ។"
    else:
        lines = [title, ""]
        for row in tasks:
            lines.append(
                f"• {str(row.get('id'))[:8]} | {row.get('status')} | {row.get('progress', 0)}% | user {row.get('telegram_user_id')}"
            )
        text = "\n".join(lines)
    await _safe_edit(query, text, back_keyboard())


async def _show_running(query) -> None:
    tasks = await supabase_service.list_tasks(status=TASK_PROCESSING, limit=10)
    queued = await supabase_service.list_tasks(status=TASK_QUEUED, limit=10)
    lines = ["🔄 Running / Queued Tasks", ""]
    for row in tasks + queued:
        lines.append(f"• {str(row.get('id'))[:8]} | {row.get('status')} | {row.get('progress', 0)}%")
    if len(lines) == 2:
        lines.append("មិនមាន task កំពុងដំណើរការ។")
    await _safe_edit(query, "\n".join(lines), back_keyboard())


async def _show_settings(query) -> None:
    text = (
        "⚙️ Settings\n\n"
        f"Max video duration: {settings.max_video_duration_seconds}s\n"
        f"Max video size: {settings.max_video_size_mb}MB\n"
        f"Max SRT size: {settings.max_srt_size_mb}MB\n"
        f"TTS provider: {settings.tts_provider}\n"
        f"TTS cache: {settings.tts_cache_enabled}\n"
        f"Keep original audio: {settings.keep_original_audio}\n"
        f"Original audio volume: {settings.original_audio_volume}\n"
        f"Dubbed audio volume: {settings.dubbed_audio_volume}\n"
        f"In-process worker: {settings.in_process_worker}\n"
        f"Worker count: {settings.in_process_worker_count}\n"
        f"Clean success files: {settings.clean_success_files}\n"
        f"Keep failed files: {settings.keep_failed_files}\n"
        f"Clear queue on startup: {settings.clear_stale_queue_on_start}\n"
        f"Queue key: {settings.redis_queue_key}"
    )
    await _safe_edit(query, text, back_keyboard())


async def _show_logs(query) -> None:
    try:
        logs = await supabase_service.recent_logs(limit=12)
        lines = ["📝 Recent Logs", ""]
        for row in logs:
            lines.append(f"• {row.get('level')} | {row.get('category')} | {truncate(row.get('message') or '', 90)}")
        if len(lines) == 2:
            lines.append("មិនទាន់មាន logs។")
        text = "\n".join(lines)
    except Exception:
        log_path = settings.app_log_file
        if log_path.exists():
            text = "📝 Recent Local Logs\n\n" + "\n".join(log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-12:])
        else:
            text = "មិនមាន logs។"
    await _safe_edit(query, text, back_keyboard())


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True when this text was consumed by the admin flow."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_admin(user.id):
        return False
    state = await redis_service.get_user_state(user.id)
    if state != STATE_ADMIN_BROADCAST_TEXT:
        return False

    text = message.text or ""
    if not text.strip():
        await message.reply_text("សូមផ្ញើអត្ថបទ Broadcast។")
        return True

    await redis_service.set(f"admin:{user.id}:broadcast_text", text, ex=60 * 30)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm Send", callback_data="admin:broadcast_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin:broadcast_cancel")],
        ]
    )
    await message.reply_text(f"តើអ្នកប្រាកដថាចង់ផ្ញើ Broadcast នេះទេ?\n\n{truncate(text, 3000)}", reply_markup=keyboard)
    return True


async def _confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin = update.effective_user
    if not query or not admin:
        return
    text = await redis_service.get(f"admin:{admin.id}:broadcast_text")
    if not text:
        await query.edit_message_text("រកមិនឃើញអត្ថបទ Broadcast។", reply_markup=back_keyboard())
        return
    await redis_service.set_user_state(admin.id, "idle")
    await redis_service.delete(f"admin:{admin.id}:broadcast_text")
    await query.edit_message_text("កំពុងផ្ញើ Broadcast...", reply_markup=back_keyboard())
    asyncio.create_task(_broadcast_to_all(context, admin.id, text, query.message.chat_id))


async def _broadcast_to_all(context: ContextTypes.DEFAULT_TYPE, admin_id: int, text: str, report_chat_id: int) -> None:
    users = await supabase_service.list_all_telegram_user_ids()
    sent = 0
    failed = 0
    for telegram_user_id in users:
        try:
            await context.bot.send_message(chat_id=telegram_user_id, text=text)
            sent += 1
        except TelegramError:
            failed += 1
        if settings.telegram_broadcast_delay_seconds > 0:
            await asyncio.sleep(settings.telegram_broadcast_delay_seconds)

    await supabase_service.create_broadcast_log(
        {
            "admin_telegram_id": admin_id,
            "message": text,
            "total_users": len(users),
            "sent_count": sent,
            "failed_count": failed,
        }
    )
    try:
        await context.bot.send_message(
            chat_id=report_chat_id,
            text=f"Broadcast រួចរាល់ ✅\nTotal: {len(users)}\nSent: {sent}\nFailed: {failed}",
        )
    except TelegramError:
        pass
