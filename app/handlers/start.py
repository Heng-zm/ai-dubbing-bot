"""Start and voice selection handlers."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.services.redis_service import redis_service
from app.services.supabase_service import supabase_service
from app.states import STATE_WAITING_VIDEO, VOICE_FEMALE, VOICE_MALE


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        try:
            await supabase_service.upsert_user(user)
        except Exception:
            pass

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("សម្រាយរឿង", callback_data="start_dubbing")]]
    )
    text = (
        "សូមស្វាគមន៍មកកាន់ Bot បញ្ចូលសម្លេងរឿង AI Dubbing 🎙️\n\n"
        "• អាច Upload វីដេអូបានអតិបរមា 1 នាទី\n"
        "• ជ្រើសរើសសម្លេង AI ភាសាខ្មែរ ប្រុស ឬ ស្រី\n"
        "• ផ្ញើឯកសារ SRT\n"
        "• Bot នឹងបង្កើតវីដេអូមានសម្លេងហើយផ្ញើត្រឡប់មកអ្នកវិញ ✅"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)


async def start_dubbing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ប្រុស - Piseth", callback_data=f"voice:{VOICE_MALE}")],
            [InlineKeyboardButton("ស្រី - Sreymom", callback_data=f"voice:{VOICE_FEMALE}")],
        ]
    )
    await query.edit_message_text("សូមជ្រើសរើសសម្លេង AI ដែលអ្នកចង់ប្រើ៖", reply_markup=keyboard)


async def voice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user:
        return
    voice = query.data.split(":", 1)[1]
    await redis_service.set_user_voice(user.id, voice)
    await redis_service.set_user_state(user.id, STATE_WAITING_VIDEO)
    try:
        await supabase_service.upsert_user(user, selected_voice=voice)
        await supabase_service.update_user_voice(user.id, voice)
    except Exception:
        pass

    await query.edit_message_text(
        "សូមផ្ញើវីដេអូរបស់អ្នក។ វីដេអូត្រូវមានរយៈពេលមិនលើសពី 1 នាទី។"
    )
