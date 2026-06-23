-- Runtime bot settings editable from Telegram /admin dashboard.

create table if not exists public.bot_settings (
    key text primary key,
    value text not null,
    value_type text not null default 'str',
    updated_by bigint,
    updated_at timestamptz not null default now()
);

insert into public.bot_settings (key, value, value_type)
values
    ('max_video_duration_seconds', '60', 'int'),
    ('max_video_size_mb', '50', 'int'),
    ('max_srt_size_mb', '2', 'int'),
    ('max_subtitle_chars', '450', 'int'),
    ('min_subtitle_duration_seconds', '0.20', 'float'),
    ('auto_srt_fixer_enabled', 'true', 'bool'),
    ('auto_srt_fixer_max_overlap_seconds', '1.2', 'float'),
    ('auto_srt_fixer_max_video_overrun_seconds', '2.0', 'float'),
    ('auto_srt_fixer_min_gap_ms', '50', 'int'),
    ('tts_provider', 'edge', 'choice'),
    ('tts_cache_enabled', 'true', 'bool'),
    ('keep_original_audio', 'false', 'bool'),
    ('original_audio_volume', '0.0', 'float'),
    ('dubbed_audio_volume', '1.0', 'float'),
    ('in_process_worker', 'true', 'bool'),
    ('in_process_worker_count', '1', 'int'),
    ('clean_success_files', 'true', 'bool'),
    ('keep_failed_files', 'true', 'bool'),
    ('clear_stale_queue_on_start', 'true', 'bool'),
    ('redis_queue_key', 'queue:dubbing', 'str'),
    ('watermark_enabled', 'true', 'bool'),
    ('watermark_text', 'Dubbed by @aidubbingkhbot', 'str'),
    ('watermark_position', 'bottom_right', 'choice'),
    ('multi_voice_enabled', 'true', 'bool'),
    ('show_processing_estimate', 'true', 'bool')
on conflict (key) do nothing;

create index if not exists idx_bot_settings_updated_at on public.bot_settings(updated_at desc);

notify pgrst, 'reload schema';
