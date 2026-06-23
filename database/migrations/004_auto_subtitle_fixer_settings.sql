-- Auto Subtitle Fixer runtime settings.
-- Run this once in Supabase SQL Editor after deploying this update.

insert into public.bot_settings (key, value, value_type)
values
    ('auto_srt_fixer_enabled', 'true', 'bool'),
    ('auto_srt_fixer_max_overlap_seconds', '1.2', 'float'),
    ('auto_srt_fixer_max_video_overrun_seconds', '2.0', 'float'),
    ('auto_srt_fixer_min_gap_ms', '50', 'int')
on conflict (key) do nothing;

notify pgrst, 'reload schema';
