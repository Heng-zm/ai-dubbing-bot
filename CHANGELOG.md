# Changelog

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
