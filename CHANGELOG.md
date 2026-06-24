
## 78% Progress Freeze Hotfix

- Added merge/upload progress heartbeat so the bot no longer appears stuck at 78%.
- Progress now moves through 80%, 84%, 88%, 91%, 92%, 95%, and 98% during ffmpeg encode and Telegram upload.
- Added a hard ffmpeg merge timeout with `FFMPEG_MERGE_TIMEOUT_SECONDS` defaulting to 420 seconds.
- Added output-file validation after ffmpeg merge.
- Added clearer logs around the audio/video merge stage.

# Changelog

## 2026-06-23 Stability + Performance Maintenance Update

### Bug fixes

- Prevent `/start` from accidentally hiding an active queued/processing task. If the user already has an active task, the bot now shows the current status instead of clearing the flow.
- SRT validation now respects admin runtime settings for max SRT size, max subtitle characters, and minimum subtitle duration.
- Added safer final delivery fallback: if Telegram rejects `send_video`, the bot retries as `send_document` before marking the task failed.
- Kept the Gemini SRT prompt in a monospace Telegram code block with safe HTML escaping.

### Performance improvements

- Runtime settings are loaded once at the beginning of the `/start` flow, avoiding stale/default limits in welcome text.
- Final-video delivery retries are more resilient to Telegram preview/codec problems.
- Existing queue deduplication, progress throttling, and TTS cache remain enabled.

### Deployment

No required Supabase schema change. Optional migration `005_maintenance_runtime_validation_settings.sql` adds default rows for the new admin-visible validation settings.


## Prompt Monospace Update

- Gemini SRT prompt now renders as a Telegram monospace/code block using HTML `<pre>`.
- Added safe HTML escaping for the copy-ready Gemini prompt.


## 2026-06-23 Production Bug Fixes + Performance Update

### Bug fixes

- Prevent duplicate Redis jobs when a user taps `ចាប់ផ្តើម Dubbing` more than once.
- Remove pending queue entries when a user cancels a task.
- Remove old pending entries before retrying a failed task.
- Restore the completion follow-up message with a `Start` button after final video delivery.
- Avoid duplicate final video sends if the follow-up `Start` message fails.
- Let the worker stop safely between major stages when a task is cancelled.
- Clean partial TTS output files before retrying a failed TTS generation.
- Improve Auto Subtitle Fixer support for Gemini outputs with `1.`, `1)`, `to`, `until`, `→`, and `➡` timing styles.

### Performance improvements

- Queue deduplication keeps single-service Render deployments stable under repeated button taps.
- Cancellation removes stale queue entries immediately, improving queue-position accuracy.
- TTS cache remains enabled for repeated subtitle lines.
- Progress updates remain throttled to reduce Telegram rate-limit issues.

### Deployment

No new SQL migration is required if migrations 001-004 have already been run.

## 2026-06-23 Hotfix: Confirm Callback NameError

### Bug fixes

- Fixed `NameError: TASK_COMPLETED is not defined` in `app/handlers/dubbing.py` when the user taps `✅ ចាប់ផ្តើម Dubbing`.
- Added a state-constant import audit check to verify all `app.states` constants used across handlers/workers are imported correctly.

### Deployment

No database migration is required. Redeploy the updated code only.

## 91% Render freeze hotfix

- Default watermark branding now uses fast MP4 metadata instead of visible `drawtext` re-encoding.
- Added admin setting `watermark_render_mode`: `metadata`, `visible`, or `off`.
- Visible watermark still works, but if it fails/timeouts the bot falls back to fast metadata branding.
- ffmpeg merge now writes to a `.partial.mp4` file and atomically moves it only after success.
- ffmpeg merge logs now show the selected plan: copy-video or encode-video.
- Startup now recovers interrupted `processing` tasks after Render restarts and shows Retry/Start buttons instead of leaving users stuck at 91%.

## Maintenance Performance Update

- Replaced blocking subprocess execution with async subprocess management.
- ffmpeg/ffprobe child processes are now terminated on timeout or cancellation.
- Added Redis pending-task dedupe set for faster queue operations and safer duplicate button handling.
- Cleaned stale Redis dedupe entries automatically when queue list no longer contains a task.
- Prevented duplicate final video delivery after restart by storing a lightweight `final_sent` marker.
- Startup recovery now marks tasks completed when the final video was already sent before a restart.
- Added `-movflags +faststart` to MP4 output for better Telegram playback behavior.

## Telegram Conflict Hotfix

- Added Redis-based single polling instance lock to prevent two copies of the bot from calling Telegram `getUpdates` at the same time.
- Added periodic lock refresh and safe lock release on shutdown.
- Added clearer logging for `telegram.error.Conflict` so it is no longer reported as a generic unhandled update exception.
- Added deployment troubleshooting instructions for duplicate Render services, old worker services, and local scripts using the same `BOT_TOKEN`.

No Supabase migration is required.
