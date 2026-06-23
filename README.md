# AI Dubbing Bot – បញ្ចូលសម្លេងរឿង AI Dubbing

Production-ready Telegram bot project for Khmer AI video dubbing. Users upload a short video and an `.srt` subtitle file. The bot generates Khmer voice using Microsoft Edge TTS / `edge-tts`, aligns audio to subtitle timing, merges it with the video using ffmpeg, and sends the final dubbed video back in Telegram.

This version supports **single-service deployment**. When `IN_PROCESS_WORKER=true`, the Telegram bot also runs the Redis queue processor inside the same process, so you do **not** need a separate Render worker service.

## Latest improvements

- Single Render service mode hardened with in-process Redis worker
- Task lock in Redis to prevent duplicate processing
- Safer progress throttling to reduce Telegram edit rate issues
- Better SRT validation and Khmer error messages
- Better video compatibility: copies MP4-safe video codecs and transcodes WebM/other codecs to H.264 MP4
- Correct volume handling: normalization no longer double-applies dubbed volume
- Optional TTS cache for repeated subtitle text
- Better `edge-tts` 403 handling with optional Azure Speech fallback
- `/status` and `/cancel` commands
- Supabase schema migration now includes `updated_at` for task updates
- Better ffmpeg subprocess errors in logs
- Startup cleanup option for old temp files

## Features

- Khmer Telegram user flow with inline buttons
- Voice selection:
  - `km-KH-PisethNeural` male voice
  - `km-KH-SreymomNeural` female voice
- Accepts Telegram video and video document uploads
- Allowed formats: `.mp4`, `.mov`, `.mkv`, `.webm`
- Video duration limit: 60 seconds by default
- Configurable max video and SRT size
- SRT format validation and subtitle timing check
- Redis state, progress, task lock, and task queue
- In-process queue worker for one-service deployment
- Optional standalone worker command for high traffic
- Supabase tables for users, tasks, broadcasts, and logs
- Telegram-only `/admin` dashboard
- Broadcast from Telegram admin panel
- ffmpeg startup checks
- TTS retry logic
- Telegram send retry logic
- Temp cleanup after successful task
- Optional tiny HTTP health server for Render Web Service deployments

## Requirements

- Python 3.11+
- Telegram Bot Token from BotFather
- Supabase project
- Redis database
- ffmpeg and ffprobe installed

## Install Python packages

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

## Install ffmpeg

### Windows

1. Download ffmpeg from https://www.gyan.dev/ffmpeg/builds/
2. Extract it, for example to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to Windows PATH
4. Test:

```bash
ffmpeg -version
ffprobe -version
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y ffmpeg
ffmpeg -version
ffprobe -version
```

### macOS

```bash
brew install ffmpeg
```

## Create Supabase tables

1. Open your Supabase project.
2. Go to SQL Editor.
3. Copy all SQL from `database/supabase_schema.sql`.
4. Run it.
5. Go to Project Settings → API.
6. Copy:
   - Project URL into `SUPABASE_URL`
   - Secret key / service role key into `SUPABASE_SERVICE_KEY`

Use a backend-only secret/service-role key. Do not use a `sb_publishable_...` key for this bot.

## Create Redis database

### Local Redis

Using Docker:

```bash
docker run --name ai-dubbing-redis -p 6379:6379 -d redis:7
```

Redis URL:

```env
REDIS_URL=redis://localhost:6379/0
```

### Render Redis / Key Value

Create a Render Key Value database and copy the internal Redis-compatible URL into `REDIS_URL`.

## Configure `.env`

```bash
cp .env.example .env
```

Minimum required values:

```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_IDS=123456789,987654321
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-secret-or-service-role-key
REDIS_URL=redis://localhost:6379/0
MAX_VIDEO_DURATION_SECONDS=60
MAX_VIDEO_SIZE_MB=50
KEEP_ORIGINAL_AUDIO=false
ORIGINAL_AUDIO_VOLUME=0.15
DUBBED_AUDIO_VOLUME=1.0

# Important for one-service mode
IN_PROCESS_WORKER=true
IN_PROCESS_WORKER_COUNT=1
```

## TTS provider settings

Default free mode:

```env
TTS_PROVIDER=edge
IN_PROCESS_WORKER_COUNT=1
EDGE_TTS_DELAY_SECONDS=6
```

If Render returns Edge TTS 403 errors, use official Azure Speech fallback:

```env
TTS_PROVIDER=auto
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=eastus
```

Or force Azure only:

```env
TTS_PROVIDER=azure
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=eastus
```

## Run bot only, with in-process queue worker

```bash
python -m app.main
```

This single command receives Telegram updates and processes dubbing tasks. You do not need to run `python run_worker.py` when `IN_PROCESS_WORKER=true`.

## Optional: run standalone worker for high traffic

For bigger traffic, disable in-process processing on the bot and run a separate worker:

```env
IN_PROCESS_WORKER=false
```

Terminal 1:

```bash
python -m app.main
```

Terminal 2:

```bash
python run_worker.py
```

## Telegram user flow

1. User sends `/start`.
2. User clicks `សម្រាយរឿង`.
3. User chooses voice gender.
4. User sends video.
5. User sends `.srt` file.
6. Bot queues a task.
7. In-process worker processes dubbing.
8. Bot sends completed video.

Useful commands:

```text
/start
/status
/cancel
/admin
```

## Admin dashboard

Send:

```text
/admin
```

Only Telegram user IDs in `ADMIN_IDS` can access it.

Admin buttons:

- 📊 Bot Stats
- 👥 Users
- 🎬 Tasks
- ✅ Completed Tasks
- ❌ Failed Tasks
- 🔄 Running Tasks
- 📢 Broadcast Message
- ⚙️ Settings
- 🧹 Clean Temp Files
- 📝 Recent Logs

## Render deployment without separate worker service

Recommended setup: deploy **one Render service** with Docker so ffmpeg is installed.

### Option A: One Render Background Worker service

This is best for Telegram polling bots because the bot does not need to expose a public HTTP server.

Render settings:

```text
New + → Background Worker
Runtime: Docker
Repository: your GitHub repo
Start Command: leave empty or use Dockerfile CMD
```

Environment variables:

```env
BOT_TOKEN=
ADMIN_IDS=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
REDIS_URL=
MAX_VIDEO_DURATION_SECONDS=60
MAX_VIDEO_SIZE_MB=50
KEEP_ORIGINAL_AUDIO=false
ORIGINAL_AUDIO_VOLUME=0.15
DUBBED_AUDIO_VOLUME=1.0
IN_PROCESS_WORKER=true
IN_PROCESS_WORKER_COUNT=1
ENABLE_HEALTH_SERVER=false
```

Start command if Render asks for one:

```bash
python -m app.main
```

### Option B: One Render Web Service

Use this only if you specifically want a Web Service. Render Web Services must bind to `$PORT`, so enable the built-in health server.

Render settings:

```text
New + → Web Service
Runtime: Docker
Repository: your GitHub repo
Start Command: leave empty or use Dockerfile CMD
```

Environment variables are the same, except:

```env
IN_PROCESS_WORKER=true
ENABLE_HEALTH_SERVER=true
```

## Render Dockerfile

The included `Dockerfile` installs ffmpeg and starts the bot:

```dockerfile
CMD ["python", "-m", "app.main"]
```

## Common errors and fixes

### `Supabase connection failed`

Fix:

1. Run `database/supabase_schema.sql` in Supabase SQL Editor.
2. Use the backend secret/service-role key in `SUPABASE_SERVICE_KEY`.
3. Do not use `sb_publishable_...` as `SUPABASE_SERVICE_KEY`.
4. Redeploy Render after env changes.

### `edge-tts 403 Invalid response status`

Fix options:

1. Keep `IN_PROCESS_WORKER_COUNT=1`.
2. Set `EDGE_TTS_DELAY_SECONDS=6` or higher.
3. Use `TTS_PROVIDER=auto` with Azure Speech keys.
4. Use `TTS_PROVIDER=azure` for production reliability.

### `ffmpeg not found`

Use Docker deployment on Render, or install ffmpeg locally and make sure `ffmpeg` and `ffprobe` are in PATH.

### Bot works but task never processes

Check:

```env
IN_PROCESS_WORKER=true
REDIS_URL=same value used by bot
```

Then check Render logs for:

```text
Started 1 in-process dubbing worker(s)
```

### Final video fails for `.webm`

This version automatically transcodes non-MP4-safe video codecs to H.264. Make sure the Docker deployment includes ffmpeg.

### Telegram upload too large

Increase:

```env
MAX_VIDEO_SIZE_MB=50
```

Remember Telegram bot download limits and Render memory/disk limits still apply.

## Project structure

```text
ai-dubbing-bot/
├── app/
│   ├── main.py
│   ├── bot.py
│   ├── config.py
│   ├── states.py
│   ├── handlers/
│   ├── services/
│   ├── workers/
│   └── utils/
├── database/supabase_schema.sql
├── temp/
├── logs/
├── requirements.txt
├── .env.example
├── Dockerfile
├── Procfile
├── README.md
└── run_worker.py
```
