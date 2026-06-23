-- Add runtime settings for smart recovery, processing estimate, watermark branding,
-- and multi-voice-per-character. Safe to run multiple times.

insert into public.bot_settings (key, value, value_type)
values
    ('watermark_enabled', 'true', 'bool'),
    ('watermark_render_mode', 'metadata', 'choice'),
    ('watermark_text', 'Dubbed by @aidubbingkhbot', 'str'),
    ('watermark_position', 'bottom_right', 'choice'),
    ('multi_voice_enabled', 'true', 'bool'),
    ('show_processing_estimate', 'true', 'bool')
on conflict (key) do nothing;

notify pgrst, 'reload schema';
