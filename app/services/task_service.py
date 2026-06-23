"""Task status helper that updates Redis and Supabase together."""

from __future__ import annotations

from typing import Any, Dict

from app.services.redis_service import redis_service
from app.services.supabase_service import supabase_service
from app.utils.time_utils import utc_now_iso


async def update_task_status(
    task_id: str,
    status: str,
    progress: int | None = None,
    error_message: str | None = None,
    output_file_path: str | None = None,
    mark_started: bool = False,
    mark_finished: bool = False,
) -> None:
    progress_value = None if progress is None else max(0, min(100, int(progress)))
    await redis_service.set_task_status(task_id, status, progress_value)
    payload: Dict[str, Any] = {"status": status}
    if progress_value is not None:
        payload["progress"] = progress_value
    if error_message:
        payload["error_message"] = error_message
    if output_file_path:
        payload["output_file_path"] = output_file_path
    if mark_started:
        payload["started_at"] = utc_now_iso()
    if mark_finished or status in {"completed", "failed", "cancelled"}:
        payload["completed_at"] = utc_now_iso()
    try:
        await supabase_service.update_task(task_id, payload)
    except Exception:
        # Redis remains the fast status source; detailed DB errors are logged elsewhere.
        pass
