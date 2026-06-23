"""Smart error classification and Khmer recovery messages."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class RecoveryInfo:
    category: str
    title: str
    user_message: str
    retry_allowed: bool
    admin_hint: str


def classify_error(exc: Exception) -> RecoveryInfo:
    text = str(exc)
    lower = text.lower()

    if "task file missing" in lower or "missing task payload path" in lower or "temp files are local" in lower:
        return RecoveryInfo(
            category="stale_file",
            title="ឯកសារចាស់បាត់",
            user_message=(
                "⚠️ រកឯកសារ Video/SRT របស់ Task នេះមិនឃើញទេ។\n\n"
                "វាអាចកើតឡើងបន្ទាប់ពី Render restart/redeploy។\n"
                "សូមចាប់ផ្តើមថ្មី ហើយផ្ញើ Video + SRT ម្តងទៀត។"
            ),
            retry_allowed=False,
            admin_hint="Local temp file disappeared. Enable Supabase Storage for durable task files.",
        )

    if "403" in lower and ("edge" in lower or "speech.platform.bing" in lower or "invalid response status" in lower):
        return RecoveryInfo(
            category="edge_tts_403",
            title="Edge TTS ត្រូវបានបដិសេធ",
            user_message=(
                "⚠️ ប្រព័ន្ធ Edge TTS កំពុងបដិសេធ request ពី server។\n\n"
                "អ្នកអាចចុច Retry ម្តងទៀត។ ប្រសិនបើនៅតែបរាជ័យ Admin គួរប្តូរ TTS provider ទៅ auto/azure។"
            ),
            retry_allowed=True,
            admin_hint="edge-tts 403/rate-limit. Use TTS_PROVIDER=auto or configure Azure Speech fallback.",
        )

    if "azure speech fallback" in lower or "azure_speech_key" in lower or "azure_speech_region" in lower:
        return RecoveryInfo(
            category="azure_config",
            title="Azure Speech មិនទាន់បានកំណត់",
            user_message=(
                "⚠️ Provider Azure ត្រូវបានជ្រើស ប៉ុន្តែ key/region មិនទាន់ត្រឹមត្រូវ។\n\n"
                "សូមព្យាយាមម្តងទៀតក្រោយ Admin កំណត់ Azure Speech។"
            ),
            retry_allowed=True,
            admin_hint="Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in Render env or switch provider to edge.",
        )

    if "ffmpeg" in lower or "ffprobe" in lower or "subprocess failed" in lower or "returned non-zero" in lower:
        return RecoveryInfo(
            category="ffmpeg",
            title="បញ្ហា ffmpeg",
            user_message=(
                "⚠️ មានបញ្ហាក្នុងការបញ្ចូលសម្លេងទៅក្នុងវីដេអូ។\n\n"
                "សូមចុច Retry ម្តងទៀត ឬផ្ញើវីដេអូជា MP4 ប្រសិនបើបរាជ័យម្តងទៀត។"
            ),
            retry_allowed=True,
            admin_hint="Check ffmpeg logs, codecs, video corruption, or Docker ffmpeg installation.",
        )

    if "invalid_srt" in lower or "subtitle" in lower or "srt" in lower:
        return RecoveryInfo(
            category="srt",
            title="បញ្ហា Subtitle",
            user_message=(
                "⚠️ ឯកសារ SRT មានបញ្ហា timing ឬ format។\n\n"
                "សូមផ្ញើ SRT ថ្មីដែល timing មិនជាន់គ្នា និងមិនលើសរយៈពេលវីដេអូ។"
            ),
            retry_allowed=False,
            admin_hint="Validate SRT numbering, timestamps, overlaps, empty subtitles, and video duration.",
        )

    if "failed to send final video" in lower or "telegram" in lower or "timed out" in lower:
        return RecoveryInfo(
            category="telegram_send",
            title="បញ្ហាផ្ញើវីដេអូទៅ Telegram",
            user_message=(
                "⚠️ វីដេអូបានបង្កើតរួច ប៉ុន្តែផ្ញើទៅ Telegram មិនបាន។\n\n"
                "សូមចុច Retry ឬបន្ទាប់មក Admin អាចពិនិត្យទំហំឯកសារ។"
            ),
            retry_allowed=True,
            admin_hint="Telegram send/upload failed. Check file size, network timeout, or output compression.",
        )

    return RecoveryInfo(
        category="unknown",
        title="បញ្ហាមិនស្គាល់",
        user_message=(
            "សូមទោស មានបញ្ហាក្នុងការដំណើរការ។\n\n"
            "អ្នកអាចចុច Retry ម្តងទៀត ឬចុច /start ដើម្បីចាប់ផ្តើមថ្មី។"
        ),
        retry_allowed=True,
        admin_hint="Unhandled worker error. Check traceback in logs.",
    )


def recovery_keyboard(task_id: str, retry_allowed: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if retry_allowed:
        rows.append([InlineKeyboardButton("🔄 ព្យាយាមម្តងទៀត", callback_data=f"dubbing:retry:{task_id}")])
    rows.append([InlineKeyboardButton("🎬 ចាប់ផ្តើមថ្មី", callback_data="start_dubbing")])
    return InlineKeyboardMarkup(rows)
