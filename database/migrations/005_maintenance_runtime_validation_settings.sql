-- Optional runtime validation settings added in the maintenance update.
-- Existing projects do not break without this migration because the code has defaults,
-- but running it makes the values visible in Supabase and consistent for /admin.

insert into public.bot_settings (key, value, value_type)
values
    ('max_subtitle_chars', '450', 'int'),
    ('min_subtitle_duration_seconds', '0.20', 'float')
on conflict (key) do nothing;

notify pgrst, 'reload schema';
