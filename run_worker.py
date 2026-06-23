"""Worker entrypoint."""

from __future__ import annotations

import asyncio

from app.workers.dubbing_worker import worker_main


if __name__ == "__main__":
    asyncio.run(worker_main())
