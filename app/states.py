"""Conversation and task state constants."""

STATE_IDLE = "idle"
STATE_WAITING_VIDEO = "waiting_video"
STATE_WAITING_SRT = "waiting_srt"
STATE_WAITING_CONFIRM = "waiting_confirm"
STATE_PROCESSING = "processing"
STATE_ADMIN_BROADCAST_TEXT = "admin_broadcast_text"

TASK_WAITING_VIDEO = "waiting_video"
TASK_WAITING_SRT = "waiting_srt"
TASK_QUEUED = "queued"
TASK_PROCESSING = "processing"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

VOICE_MALE = "km-KH-PisethNeural"
VOICE_FEMALE = "km-KH-SreymomNeural"

VOICE_LABELS = {
    VOICE_MALE: "ប្រុស - Piseth",
    VOICE_FEMALE: "ស្រី - Sreymom",
}
