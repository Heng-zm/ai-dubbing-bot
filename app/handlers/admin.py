"""Telegram-only admin dashboard."""

from __future__ import annotations

import asyncio
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import settings
from app.services.redis_service import redis_service
from app.services.runtime_settings import SETTING_DEFS, display_value, runtime_settings
from app.services.supabase_service import supabase_service
from app.states import (
    STATE_ADMIN_BROADCAST_TEXT,
    STATE_ADMIN_SETTING_VALUE,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_PROCESSING,
    TASK_QUEUED,
)
from app.utils.file_utils import clean_temp_older_than
from app.utils.text_utils import truncate
from app.utils.telegram_ui import bool_badge, percent_line, status_emoji, status_label



SETTING_UI_LABELS = {
    "max_video_duration_seconds": "⏱ Max video duration",
    "max_video_size_mb": "📦 Max video size",
    "max_srt_size_mb": "📝 Max SRT size",
    "tts_provider": "🎙️ TTS provider",
    "tts_cache_enabled": "⚡ TTS cache",
    "keep_original_audio": "🎚 Keep original audio",
    "original_audio_volume": "🔉 Original audio volume",
    "dubbed_audio_volume": "🔊 Dubbed audio volume",
    "in_process_worker": "⚙️ In-process worker",
    "in_process_worker_count": "👷 Worker count",
    "clean_success_files": "🧹 Clean success files",
    "keep_failed_files": "🧪 Keep failed files",
    "clear_stale_queue_on_start": "🧾 Clear queue on startup",
    "redis_queue_key": "🔑 Queue key",
}


def _setting_label(key: str) -> str:
    definition = SETTING_DEFS.get(key)
    return SETTING_UI_LABELS.get(key, definition.label if definition else key)


def _setting_value(key: str, value: Any) -> str:
    definition = SETTING_DEFS.get(key)
    if definition and definition.type == "bool":
        return bool_badge(value)
    if key in {"max_video_duration_seconds"}:
        return f"{display_value(key, value)}s"
    if key in {"max_video_size_mb", "max_srt_size_mb"}:
        return f"{display_value(key, value)}MB"
    return display_value(key, value)

SETTING_ORDER = [
    "max_video_duration_seconds",
    "max_video_size_mb",
    "max_srt_size_mb",
    "tts_provider",
    "tts_cache_enabled",
    "keep_original_audio",
    "original_audio_volume",
    "dubbed_audio_volume",
    "in_process_worker",
    "in_process_worker_count",
    "clean_success_files",
    "keep_failed_files",
    "clear_stale_queue_on_start",
    "redis_queue_key",
]


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.admin_ids)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 ស្ថិតិ Bot", callback_data="admin:stats")],
            [InlineKeyboardButton("👥 អ្នកប្រើ", callback_data="admin:users"), InlineKeyboardButton("🎬 Tasks", callback_data="admin:tasks")],
            [InlineKeyboardButton("✅ រួចរាល់", callback_data="admin:completed"), InlineKeyboardButton("❌ បរាជ័យ", callback_data="admin:failed")],
            [InlineKeyboardButton("🔄 កំពុងដំណើរការ", callback_data="admin:running")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin:settings"), InlineKeyboardButton("🧹 Clean Temp", callback_data="admin:clean")],
            [InlineKeyboardButton("🧾 Clear Queue", callback_data="admin:clear_queue"), InlineKeyboardButton("📝 Logs", callback_data="admin:logs")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data="admin:home")]])


def settings_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ ត្រឡប់ទៅ Settings", callback_data="admin:settings")],
            [InlineKeyboardButton("🏠 Admin Home", callback_data="admin:home")],
        ]
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id if user else None):
        await update.effective_message.reply_text("អ្នកមិនមានសិទ្ធិប្រើ Admin Dashboard ទេ។")
        return
    await update.effective_message.reply_text("🏠 Admin Dashboard\n\nជ្រើសរើសមុខងារដែលចង់គ្រប់គ្រង៖", reply_markup=admin_keyboard())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query:
        return
    if not is_admin(user.id if user else None):
        await query.answer("No permission", show_alert=True)
        return
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "home"

    if action == "home":
        await redis_service.set_user_state(user.id, "idle")
        await redis_service.delete(f"admin:{user.id}:setting_key")
        await query.edit_message_text("🏠 Admin Dashboard\n\nជ្រើសរើសមុខងារដែលចង់គ្រប់គ្រង៖", reply_markup=admin_keyboard())
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
        await query.edit_message_text("📢 Broadcast Message\n\nសូមផ្ញើអត្ថបទដែលអ្នកចង់ផ្ញើទៅអ្នកប្រើទាំងអស់។\n\nអ្នកនឹងឃើញប៊ូតុង Confirm មុនពេលផ្ញើពិត។", reply_markup=back_keyboard())
    elif action == "settings":
        await redis_service.set_user_state(user.id, "idle")
        await redis_service.delete(f"admin:{user.id}:setting_key")
        await _show_settings(query)
    elif action == "setting" and len(parts) >= 3:
        await _show_setting_detail(query, parts[2])
    elif action == "edit" and len(parts) >= 3:
        await _ask_setting_value(query, user.id, parts[2])
    elif action == "toggle" and len(parts) >= 3:
        await _toggle_bool_setting(query, user.id, parts[2])
    elif action == "choice" and len(parts) >= 3:
        await _show_choice_setting(query, parts[2])
    elif action == "choiceval" and len(parts) >= 4:
        await _set_choice_setting(query, user.id, parts[2], parts[3])
    elif action == "reset" and len(parts) >= 3:
        await _reset_setting(query, parts[2])
    elif action == "clean":
        deleted = clean_temp_older_than(hours=settings.cleanup_old_temp_hours)
        await query.edit_message_text(f"🧹 សម្អាតរួចរាល់ ✅\n\nបានលុប temp files ចំនួន {deleted} files។", reply_markup=back_keyboard())
    elif action == "clear_queue":
        count = await redis_service.purge_queue()
        await query.edit_message_text(f"🧾 Clear Queue រួចរាល់ ✅\n\nបានលុប Redis queue ចំនួន {count} job(s)។", reply_markup=back_keyboard())
    elif action == "logs":
        await _show_logs(query)
    elif action == "broadcast_confirm":
        await _confirm_broadcast(update, context)
    elif action == "broadcast_cancel":
        await redis_service.set_user_state(user.id, "idle")
        await redis_service.delete(f"admin:{user.id}:broadcast_text")
        await query.edit_message_text("📢 Broadcast ត្រូវបានបោះបង់។", reply_markup=back_keyboard())
    else:
        await query.edit_message_text("Unknown admin action.", reply_markup=back_keyboard())


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
        f"🧠 Redis: {'OK ✅' if redis_ok else 'FAILED ❌'}\n"
        f"🗄️ Supabase: {'OK ✅' if supabase_ok else 'FAILED ❌'}"
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
    title = "🎬 Recent Tasks" if not status else f"🎬 Tasks: {status_label(status)}"
    if not tasks:
        text = f"{title}\n\nមិនមានទិន្នន័យទេ។"
    else:
        lines = [title, ""]
        for row in tasks:
            lines.append(
                f"• {status_emoji(row.get('status'))} {str(row.get('id'))[:8]} | {status_label(row.get('status'))} | {percent_line(row.get('progress', 0))} | user {row.get('telegram_user_id')}"
            )
        text = "\n".join(lines)
    await _safe_edit(query, text, back_keyboard())


async def _show_running(query) -> None:
    tasks = await supabase_service.list_tasks(status=TASK_PROCESSING, limit=10)
    queued = await supabase_service.list_tasks(status=TASK_QUEUED, limit=10)
    lines = ["🔄 Running / Queued Tasks", ""]
    for row in tasks + queued:
        lines.append(f"• {status_emoji(row.get('status'))} {str(row.get('id'))[:8]} | {status_label(row.get('status'))} | {percent_line(row.get('progress', 0))}")
    if len(lines) == 2:
        lines.append("មិនមាន task កំពុងដំណើរការ។")
    await _safe_edit(query, "\n".join(lines), back_keyboard())


def _settings_keyboard(values: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in SETTING_ORDER:
        definition = SETTING_DEFS[key]
        value = _setting_value(key, values.get(key, definition.default))
        label = f"{_setting_label(key)}: {value}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"admin:setting:{key}")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="admin:settings")])
    rows.append([InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


async def _show_settings(query) -> None:
    values = await runtime_settings.load(force=True)
    lines = [
        "⚙️ Bot Settings",
        "",
        "Admin អាចកែ setting ដោយផ្ទាល់នៅទីនេះ។",
        "Secrets ដូចជា BOT_TOKEN, Supabase key និង Redis URL នៅតែរក្សាក្នុង Render Environment។",
        "",
        "📌 Current Settings",
    ]
    for key in SETTING_ORDER:
        lines.append(f"• {_setting_label(key)}: {_setting_value(key, values.get(key, SETTING_DEFS[key].default))}")
    lines.extend([
        "",
        "ចុច setting ខាងក្រោមដើម្បីកែ។",
        "⚠️ Setting ខ្លះត្រូវការ redeploy/restart ដើម្បីអនុវត្តពេញលេញ។",
    ])
    text = "\n".join(lines)
    await _safe_edit(query, text, _settings_keyboard(values))


def _setting_detail_keyboard(key: str) -> InlineKeyboardMarkup:
    definition = SETTING_DEFS[key]
    rows: list[list[InlineKeyboardButton]] = []
    if definition.type == "bool":
        rows.append([InlineKeyboardButton("🔁 Toggle", callback_data=f"admin:toggle:{key}")])
    elif definition.type == "choice":
        rows.append([InlineKeyboardButton("🎚️ Choose Value", callback_data=f"admin:choice:{key}")])
    else:
        rows.append([InlineKeyboardButton("✏️ Edit Value", callback_data=f"admin:edit:{key}")])
    rows.append([InlineKeyboardButton("♻️ Reset Default", callback_data=f"admin:reset:{key}")])
    rows.append([InlineKeyboardButton("⬅️ ត្រឡប់ទៅ Settings", callback_data="admin:settings")])
    return InlineKeyboardMarkup(rows)


async def _show_setting_detail(query, key: str) -> None:
    definition = SETTING_DEFS.get(key)
    if not definition:
        await query.edit_message_text("Setting មិនត្រឹមត្រូវ។", reply_markup=settings_back_keyboard())
        return
    values = await runtime_settings.load()
    value = values.get(key, definition.default)
    restart_note = "\n⚠️ Setting នេះត្រូវការ restart/redeploy ដើម្បីអនុវត្តពេញលេញ។" if definition.restart_required else ""
    text = (
        f"⚙️ {_setting_label(key)}\n\n"
        f"Current: {_setting_value(key, value)}\n"
        f"Default: {_setting_value(key, definition.default)}\n"
        f"Type: {definition.type}\n"
        f"Note: {definition.description or 'N/A'}"
        f"{restart_note}"
    )
    await _safe_edit(query, text, _setting_detail_keyboard(key))


async def _ask_setting_value(query, admin_id: int, key: str) -> None:
    definition = SETTING_DEFS.get(key)
    if not definition:
        await query.edit_message_text("Setting មិនត្រឹមត្រូវ។", reply_markup=settings_back_keyboard())
        return
    await redis_service.set_user_state(admin_id, STATE_ADMIN_SETTING_VALUE)
    await redis_service.set(f"admin:{admin_id}:setting_key", key, ex=60 * 10)
    range_text = ""
    if definition.min_value is not None or definition.max_value is not None:
        range_text = f"\nRange: {definition.min_value} - {definition.max_value}"
    await query.edit_message_text(
        f"✏️ កែ {_setting_label(key)}\n\nសូមផ្ញើ value ថ្មី។{range_text}\nCurrent type: {definition.type}",
        reply_markup=settings_back_keyboard(),
    )


async def _toggle_bool_setting(query, admin_id: int, key: str) -> None:
    values = await runtime_settings.load()
    current = bool(values.get(key, False))
    await _save_setting_and_show(query, admin_id, key, "false" if current else "true")


async def _show_choice_setting(query, key: str) -> None:
    definition = SETTING_DEFS.get(key)
    if not definition or definition.type != "choice":
        await query.edit_message_text("Setting នេះមិនមែនជា choice ទេ។", reply_markup=settings_back_keyboard())
        return
    rows = [[InlineKeyboardButton(choice, callback_data=f"admin:choiceval:{key}:{choice}")] for choice in definition.choices]
    rows.append([InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data=f"admin:setting:{key}")])
    await query.edit_message_text(f"🎚️ ជ្រើសរើស {_setting_label(key)}", reply_markup=InlineKeyboardMarkup(rows))


async def _set_choice_setting(query, admin_id: int, key: str, value: str) -> None:
    await _save_setting_and_show(query, admin_id, key, value)


async def _save_setting_and_show(query, admin_id: int, key: str, raw_value: str) -> None:
    try:
        value, definition = await runtime_settings.set_value(key, raw_value, admin_id)
    except Exception as exc:
        await query.edit_message_text(
            "មិនអាចរក្សាទុក setting បានទេ។\n\n"
            f"Error: {truncate(str(exc), 800)}\n\n"
            "សូមប្រាកដថាបាន run database/migrations/002_add_bot_settings.sql ក្នុង Supabase។",
            reply_markup=settings_back_keyboard(),
        )
        return
    restart_note = "\n\n⚠️ សូម redeploy/restart bot ដើម្បីអនុវត្ត setting នេះពេញលេញ។" if definition.restart_required else ""
    await query.edit_message_text(
        f"បានរក្សាទុក ✅\n\n{_setting_label(key)}: {_setting_value(key, value)}{restart_note}",
        reply_markup=settings_back_keyboard(),
    )


async def _reset_setting(query, key: str) -> None:
    try:
        definition = await runtime_settings.reset_value(key)
    except Exception as exc:
        await query.edit_message_text(f"Reset failed: {truncate(str(exc), 800)}", reply_markup=settings_back_keyboard())
        return
    await query.edit_message_text(
        f"បាន reset ទៅ default ✅\n\n{_setting_label(key)}: {_setting_value(key, definition.default)}",
        reply_markup=settings_back_keyboard(),
    )


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
    """Return True when this text was consumed by an admin-only flow."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_admin(user.id):
        return False
    state = await redis_service.get_user_state(user.id)

    if state == STATE_ADMIN_SETTING_VALUE:
        key = await redis_service.get(f"admin:{user.id}:setting_key")
        if not key:
            await redis_service.set_user_state(user.id, "idle")
            await message.reply_text("រកមិនឃើញ setting ដែលកំពុងកែ។ សូមចូល /admin → Settings ម្តងទៀត។")
            return True
        raw_value = (message.text or "").strip()
        try:
            value, definition = await runtime_settings.set_value(key, raw_value, user.id)
        except Exception as exc:
            await message.reply_text(
                "Value មិនត្រឹមត្រូវ ឬមិនអាចរក្សាទុកបានទេ។\n\n"
                f"Error: {truncate(str(exc), 1000)}\n\n"
                "សូមព្យាយាមម្តងទៀត ឬ run database/migrations/002_add_bot_settings.sql។",
                reply_markup=settings_back_keyboard(),
            )
            return True
        await redis_service.set_user_state(user.id, "idle")
        await redis_service.delete(f"admin:{user.id}:setting_key")
        restart_note = "\n\n⚠️ Setting នេះត្រូវការ redeploy/restart ដើម្បីអនុវត្តពេញលេញ។" if definition.restart_required else ""
        await message.reply_text(
            f"បានរក្សាទុក setting ✅\n\n{_setting_label(key)}: {_setting_value(key, value)}{restart_note}",
            reply_markup=settings_back_keyboard(),
        )
        return True

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
    await query.edit_message_text("📢 កំពុងផ្ញើ Broadcast...", reply_markup=back_keyboard())
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
            text=f"📢 Broadcast រួចរាល់ ✅\n\nTotal: {len(users)}\nSent: {sent}\nFailed: {failed}",
        )
    except TelegramError:
        pass
