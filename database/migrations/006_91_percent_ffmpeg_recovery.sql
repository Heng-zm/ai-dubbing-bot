-- 91% ffmpeg/render recovery update.
-- This adds a fast watermark render mode setting. Default 'metadata' avoids
-- visible drawtext re-encode on Render while preserving branding in MP4 metadata.

insert into public.bot_settings (key, value, value_type)
values
    ('watermark_render_mode', 'metadata', 'choice')
on conflict (key) do nothing;

notify pgrst, 'reload schema';
