"""Start, help, and voice selection handlers."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config import settings
from app.services.logger_service import logger
from app.services.redis_service import redis_service
from app.services.runtime_settings import runtime_settings
from app.services.supabase_service import supabase_service
from app.states import STATE_WAITING_VIDEO, VOICE_FEMALE, VOICE_MALE
from app.utils.telegram_ui import step_title


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset the user's flow and show a clean Khmer welcome screen."""
    user = update.effective_user
    if user:
        await redis_service.clear_user_flow(user.id)
        try:
            await supabase_service.upsert_user(user)
        except Exception as exc:
            logger.warning("Could not upsert user on /start: %s", exc)

    runtime = runtime_settings.cached()
    max_duration = runtime.get("max_video_duration_seconds", settings.max_video_duration_seconds)
    max_video_size = runtime.get("max_video_size_mb", settings.max_video_size_mb)
    max_srt_size = runtime.get("max_srt_size_mb", settings.max_srt_size_mb)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎙️ ចាប់ផ្តើម Dubbing", callback_data="start_dubbing")],
            [InlineKeyboardButton("ℹ️ របៀបប្រើ", callback_data="start_help")],
        ]
    )
    text = (
        "🎙️ សូមស្វាគមន៍មកកាន់ Bot បញ្ចូលសម្លេងរឿង AI Dubbing\n\n"
        "Bot នេះជួយបង្កើតសម្លេង AI ភាសាខ្មែរ តាម Subtitle SRT ហើយបញ្ចូលទៅក្នុងវីដេអូរបស់អ្នក។\n\n"
        "✅ អ្វីដែលអ្នកត្រូវការ\n"
        f"• វីដេអូខ្លី មិនលើស {max_duration} វិនាទី\n"
        f"• ទំហំវីដេអូ មិនលើស {max_video_size}MB\n"
        f"• ឯកសារ Subtitle .srt មិនលើស {max_srt_size}MB\n"
        "• ជ្រើសសម្លេង ប្រុស ឬ ស្រី\n\n"
        "ចុចប៊ូតុងខាងក្រោម ដើម្បីចាប់ផ្តើម។"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a concise user help screen."""
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎙️ ចាប់ផ្តើម Dubbing", callback_data="start_dubbing")]])
    text = (
        "ℹ️ របៀបប្រើ AI Dubbing Bot\n\n"
        "1️⃣ ចុច /start ហើយចុច 🎙️ ចាប់ផ្តើម Dubbing\n"
        "2️⃣ ជ្រើសសម្លេង AI ខ្មែរ ប្រុស ឬ ស្រី\n"
        "3️⃣ ផ្ញើវីដេអូ mp4/mov/mkv/webm\n"
        "4️⃣ ផ្ញើឯកសារ .srt ជា Document\n"
        "5️⃣ ពិនិត្យ Subtitle Preview រួចចុច ចាប់ផ្តើម Dubbing ✅\n\n"
        "ពាក្យបញ្ជា៖\n"
        "• /status មើលស្ថានភាព Task\n"
        "• /cancel បោះបង់ Task បច្ចុប្បន្ន\n"
        "• /admin សម្រាប់ Admin ប៉ុណ្ណោះ"
    )
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎙️ ចាប់ផ្តើម Dubbing", callback_data="start_dubbing")],
            [InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data="start_home")],
        ]
    )
    text = (
        "ℹ️ របៀបប្រើ\n\n"
        "1️⃣ ជ្រើសសម្លេង AI\n"
        "2️⃣ ផ្ញើវីដេអូខ្លី\n"
        "3️⃣ ផ្ញើឯកសារ .srt\n"
        "4️⃣ ពិនិត្យ preview\n"
        "5️⃣ ចុចចាប់ផ្តើម ហើយរង់ចាំវីដេអូរួចរាល់\n\n"
        "បើមាន Task កំពុងដំណើរការ ប្រើ /status ដើម្បីមើលស្ថានភាព។"
    )
    await query.edit_message_text(text, reply_markup=keyboard)


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def start_dubbing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👨 ប្រុស - Piseth", callback_data=f"voice:{VOICE_MALE}")],
            [InlineKeyboardButton("👩 ស្រី - Sreymom", callback_data=f"voice:{VOICE_FEMALE}")],
            [InlineKeyboardButton("⬅️ ត្រឡប់ក្រោយ", callback_data="start_home")],
        ]
    )
    text = (
        f"{step_title(1, 4, 'ជ្រើសសម្លេង AI')}\n\n"
        "សូមជ្រើសសម្លេងដែលចង់ប្រើសម្រាប់វីដេអូនេះ៖\n\n"
        "👨 Piseth — សម្លេងបុរស ខ្មែរ\n"
        "👩 Sreymom — សម្លេងស្ត្រី ខ្មែរ"
    )
    await query.edit_message_text(text, reply_markup=keyboard)


async def voice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = update.effective_user
    if not user:
        return
    voice = query.data.split(":", 1)[1]
    if voice not in {VOICE_MALE, VOICE_FEMALE}:
        await query.edit_message_text("សម្លេងនេះមិនត្រឹមត្រូវទេ។ សូមចុច /start ហើយជ្រើសម្តងទៀត។")
        return

    await redis_service.set_user_voice(user.id, voice)
    await redis_service.set_user_state(user.id, STATE_WAITING_VIDEO)
    try:
        await supabase_service.upsert_user(user, selected_voice=voice)
        await supabase_service.update_user_voice(user.id, voice)
    except Exception as exc:
        logger.warning("Could not save selected voice: %s", exc)

    runtime = runtime_settings.cached()
    max_duration = runtime.get("max_video_duration_seconds", settings.max_video_duration_seconds)
    max_video_size = runtime.get("max_video_size_mb", settings.max_video_size_mb)
    await query.edit_message_text(
        f"{step_title(2, 4, 'ផ្ញើវីដេអូ')}\n\n"
        "សូមផ្ញើវីដេអូរបស់អ្នកឥឡូវនេះ។\n\n"
        f"✅ រយៈពេលអតិបរមា: {max_duration} វិនាទី\n"
        f"✅ ទំហំអតិបរមា: {max_video_size}MB\n"
        "✅ Format: mp4, mov, mkv, webm\n\n"
        "អ្នកអាចផ្ញើជា Video ឬ Document video បាន។"
    )
