# AI Dubbing Bot – បញ្ចូលសម្លេងរឿង AI Dubbing

Production-ready Telegram bot project for Khmer AI video dubbing. Users upload a short video and an `.srt` subtitle file. The bot generates Khmer voice using Microsoft Edge TTS, aligns audio to subtitle timing, merges it with the video using ffmpeg, and sends the final dubbed video back in Telegram.

This version supports **single-service deployment**. When `IN_PROCESS_WORKER=true`, the Telegram bot also runs the Redis queue processor inside the same process, so you do **not** need a separate Render worker service.

## Features

- Khmer Telegram user flow with inline buttons
- Voice selection:
  - `km-KH-PisethNeural` male voice
  - `km-KH-SreymomNeural` female voice
- Accepts Telegram video and video document uploads
- Allowed formats: `.mp4`, `.mov`, `.mkv`, `.webm`
- Video duration limit: 60 seconds by default
- Configurable max video size
- SRT validation and subtitle timing check
- Redis state, progress, and task queue
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
   - Service Role key into `SUPABASE_SERVICE_KEY`

Use the service role key only on your server. Do not expose it in frontend apps.

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

Fill values:

```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_IDS=123456789,987654321
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
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

## Run bot only, with in-process queue worker

```bash
python -m app.main
```

This single command receives Telegram updates and processes dubbing tasks. You do not need to run `python run_worker.py` when `IN_PROCESS_WORKER=true`.

## Optional: run standalone worker for high traffic

For bigger traffic, you can disable in-process processing on the bot and run a separate worker:

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
2. User clicks `🎙️ ចាប់ផ្តើម Dubbing`.
3. User chooses voice gender.
4. User sends video.
5. User sends `.srt` file.
6. Bot queues a task.
7. In-process worker processes dubbing.
8. Bot sends completed video.

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

The bot will still use Telegram polling, and the tiny health server only responds to `/`, `/health`, and `/healthz` for Render health checks.

## Render deployment steps

1. Push this project to GitHub.
2. Create Supabase project and run `database/supabase_schema.sql`.
3. Create Render Key Value database and copy its internal Redis URL.
4. Create one Render service using Docker.
5. Add all env variables.
6. Deploy.
7. Check logs for:

```text
ffmpeg found
Redis=True
Supabase=True
Started 1 in-process dubbing worker(s)
Starting AI Dubbing Bot with polling
```

## Audio behavior

- TTS is generated per subtitle block.
- Silence is inserted between subtitle segments.
- If TTS is shorter than the subtitle window, silence is padded.
- If TTS is longer, it is carefully sped up with ffmpeg `atempo` and trimmed to the subtitle duration.
- Final dubbed audio is normalized.
- Final video duration is kept aligned with original video.

## Common errors and fixes

### `Missing required binary: ffmpeg, ffprobe`

Use Docker deployment. This project includes a Dockerfile that installs ffmpeg.

### `Supabase connection failed`

Check:

- Did you run `database/supabase_schema.sql`?
- Is `SUPABASE_URL` correct?
- Are you using `SUPABASE_SERVICE_KEY`, not anon key?

### `Redis connection failed`

Check Redis URL and make sure Redis is running.

Local Docker test:

```bash
docker ps
```

### Bot receives video but task never processes

Make sure this env var is set:

```env
IN_PROCESS_WORKER=true
```

Also check Redis is connected and the Render logs show:

```text
Started 1 in-process dubbing worker(s)
```

### Render Web Service deploy fails because no open port

Set:

```env
ENABLE_HEALTH_SERVER=true
```

For a Background Worker service, keep it false.

### Free Render service sleeps

If using a free Web Service, it can spin down when idle. For a Telegram bot that must respond reliably, use a paid instance or a Render Background Worker.


## Render crash: Supabase connection failed

If Render logs show:

```text
RuntimeError: Supabase connection failed
```

This is not related to worker service mode. It means the bot cannot read the Supabase tables during startup. Fix it like this:

1. In Supabase, open **SQL Editor**.
2. Run the full file: `database/supabase_schema.sql`.
3. In Render, open your service → **Environment**.
4. Check these variables exactly:

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

Use the **service_role** key, not the anon/public key. Do not add quotes around the values.

After updating env vars, click **Manual Deploy → Clear build cache & deploy**.

For temporary debugging only, you can set:

```env
ALLOW_START_WITHOUT_SUPABASE=true
```

The bot will start, but database features will still fail until Supabase is fixed. Keep it `false` in production.

## Fix: edge-tts 403 on Render

If Render logs show:

```text
TTS attempt failed: 403, message='Invalid response status', url='wss://speech.platform.bing.com/...'
```

this means the Microsoft Edge Read Aloud WebSocket endpoint rejected the request. This can happen when the `edge-tts` package is old, when Microsoft changes the Edge endpoint, when a cloud IP is rate-limited, or when too many TTS requests run at the same time.

This project includes these protections:

```env
edge-tts==7.2.8
IN_PROCESS_WORKER_COUNT=1
EDGE_TTS_DELAY_SECONDS=6
TTS_MAX_RETRIES=3
TTS_RETRY_BASE_DELAY_SECONDS=3
```

Recommended Render env values:

```env
TTS_PROVIDER=edge
IN_PROCESS_WORKER=true
IN_PROCESS_WORKER_COUNT=1
EDGE_TTS_DELAY_SECONDS=6
TTS_MAX_RETRIES=3
```

Then redeploy with **Clear build cache & deploy** so Render installs the newer edge-tts version.

### If 403 still continues

The Edge Read Aloud endpoint is unofficial and can block cloud hosting IPs. For production stability, use the official Azure Speech fallback:

```env
TTS_PROVIDER=auto
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=eastus
```

`auto` tries free `edge-tts` first. If Edge returns 403/blocking, it falls back to Azure Speech.

You can also force Azure only:

```env
TTS_PROVIDER=azure
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=eastus
```

The Khmer voice names remain the same:

```text
km-KH-PisethNeural
km-KH-SreymomNeural
```

## v1.2.0 update: queue position, SRT preview, retry failed tasks

This build adds three production UX features:

### 1. Queue position for users

After the user confirms the SRT preview, the bot enqueues the job and shows the estimated position:

```text
✅ បានដាក់ចូល Queue រួចហើយ
ការងាររបស់អ្នកស្ថិតនៅជួរទី 2។
```

Users can also run:

```text
/status
```

When the task is still pending in Redis, `/status` shows the live queue position.

### 2. Subtitle preview before processing

The bot no longer starts processing immediately after SRT upload. It first validates the SRT and shows:

- subtitle count
- last subtitle timing
- video duration
- selected Khmer voice
- total character count
- first subtitle preview lines
- expected queue position

User buttons:

```text
[ ចាប់ផ្តើម Dubbing ✅ ]
[ ផ្លាស់ប្តូរ SRT 🔁 ]
[ បោះបង់ ❌ ]
```

This prevents wasting TTS/ffmpeg time when the wrong SRT file is uploaded.

### 3. Resume failed task button

When processing fails and `KEEP_FAILED_FILES=true`, the bot shows:

```text
[ ព្យាយាមម្តងទៀត 🔄 ]
[ ចាប់ផ្តើមថ្មី 🎬 ]
```

Retry works when the original video and SRT temp files still exist. If Render restarted/redeployed and the temp files disappeared, the bot will safely tell the user to upload again.

Recommended production env:

```env
KEEP_FAILED_FILES=true
CLEAR_STALE_QUEUE_ON_START=true
IN_PROCESS_WORKER=true
IN_PROCESS_WORKER_COUNT=1
```

## Update: Admin-configurable bot settings

Operational bot settings are no longer required in Render `.env`. Keep only secrets and infrastructure in Render environment:

```env
BOT_TOKEN=
ADMIN_IDS=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
REDIS_URL=
ENABLE_HEALTH_SERVER=true
```

Then configure runtime settings inside Telegram:

```text
/admin → ⚙️ Settings
```

Admin can edit:

```text
Max video duration: 60s
Max video size: 50MB
Max SRT size: 2MB
TTS provider: edge
TTS cache: True
Keep original audio: False
Original audio volume: 0.0
Dubbed audio volume: 1.0
In-process worker: True
Worker count: 1
Clean success files: True
Keep failed files: True
Clear queue on startup: True
Queue key: queue:dubbing
```

Run this migration once in Supabase SQL Editor:

```sql
-- database/migrations/002_add_bot_settings.sql
```

Settings are stored in `public.bot_settings` and cached in Redis. Most settings apply immediately. Settings marked restart-required, such as worker count and clear queue on startup, apply fully after a Render redeploy/restart.

## UX / Flow Update

This version includes a cleaner Telegram experience:

- `/start` now shows a polished Khmer welcome screen with clear requirements.
- Added `/help` and an inline “របៀបប្រើ” help screen.
- Step labels guide users through the flow:
  1. Choose voice
  2. Send video
  3. Send SRT
  4. Confirm subtitle preview
- SRT preview now uses cleaner wording and shows timing status.
- Queue and processing messages now use progress bars.
- `/status` now shows Khmer status labels, emoji status, progress bar, and queue position.
- Failed task retry buttons have clearer text.
- Admin dashboard buttons and settings labels are cleaner and easier to scan.
- Broadcast and cleanup confirmations use clearer wording.

No new environment variables are required for this UX update.

## Update: Smart Recovery, Estimate, Watermark, Multi Voice

This version adds four production features:

### 1. Smart Error Recovery

When a task fails, the worker classifies the error and shows a useful Khmer recovery message instead of a generic failure.

Detected categories include:

- `edge_tts_403` — Edge TTS blocked/rate limited the Render server
- `azure_config` — Azure fallback is selected but key/region is missing
- `ffmpeg` — video/audio merge or codec problem
- `srt` — bad subtitle timing or SRT format
- `telegram_send` — final video created but Telegram upload failed
- `stale_file` — Render temp file disappeared after restart/redeploy
- `unknown` — fallback category with retry button

The user sees the right button:

```text
[ 🔄 ព្យាយាមម្តងទៀត ]
[ 🎬 ចាប់ផ្តើមថ្មី ]
```

For stale missing files, retry is hidden because local Render temp files cannot be recovered.

### 2. Processing Time Estimate

Before processing, the SRT preview now shows estimated processing time based on:

- Video duration
- Subtitle count
- Total subtitle characters
- Queue length
- TTS provider: edge / auto / azure

`/status` also shows the estimate while the task is queued or processing.

Admin can toggle this in:

```text
/admin → ⚙️ Settings → ⏱ Time estimate
```

### 3. Watermark / Branding

Final videos include a branding watermark by default:

```text
Dubbed by @aidubbingkhbot
```

Admin settings:

```text
/admin → ⚙️ Settings → 🏷 Watermark
/admin → ⚙️ Settings → ✍️ Watermark text
/admin → ⚙️ Settings → 📍 Watermark position
```

Supported positions:

```text
bottom_right
bottom_left
top_right
top_left
```

Note: watermark uses ffmpeg `drawtext`, so video must be re-encoded. If watermarking fails because the host ffmpeg lacks drawtext/font support, the bot retries automatically without watermark so the user still receives the video.

### 4. Multi Voice Per Character

Users can write character labels in SRT. The bot strips the label from spoken text and uses the matching voice.

Examples:

```srt
1
00:00:00,000 --> 00:00:02,000
[boy] សួស្តី!

2
00:00:02,100 --> 00:00:04,000
[girl] ចាស៎!
```

Also supported:

```text
boy: Hello
girl: Hello
ប្រុស: សួស្តី
ស្រី: សួស្តី
```

Default mapping:

```text
boy / male / ប្រុស → km-KH-PisethNeural
girl / female / ស្រី → km-KH-SreymomNeural
unknown label → user's selected default voice
```

Admin can toggle this in:

```text
/admin → ⚙️ Settings → 👥 Multi voice
```

## Required Supabase Migration

Run this migration after deploying this update:

```text
database/migrations/003_smart_recovery_estimate_watermark_multivoice.sql
```

Or rerun the full schema:

```text
database/supabase_schema.sql
```
