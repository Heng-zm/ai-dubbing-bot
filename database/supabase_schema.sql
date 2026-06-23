-- AI Dubbing Bot Supabase schema
-- Run this file in Supabase SQL Editor.

create extension if not exists "pgcrypto";

create table if not exists public.users (
    id uuid primary key default gen_random_uuid(),
    telegram_user_id bigint not null unique,
    username text,
    first_name text,
    last_name text,
    language_code text,
    selected_voice text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_active_at timestamptz not null default now()
);

create table if not exists public.dubbing_tasks (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.users(id) on delete set null,
    telegram_user_id bigint not null,
    status text not null default 'waiting_video' check (
        status in ('waiting_video', 'waiting_srt', 'queued', 'processing', 'completed', 'failed', 'cancelled')
    ),
    voice text,
    video_file_id text,
    video_file_path text,
    srt_file_id text,
    srt_file_path text,
    output_file_path text,
    video_duration numeric,
    file_size bigint,
    progress int not null default 0,
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz
);

create table if not exists public.broadcasts (
    id uuid primary key default gen_random_uuid(),
    admin_telegram_id bigint not null,
    message text not null,
    total_users int not null default 0,
    sent_count int not null default 0,
    failed_count int not null default 0,
    created_at timestamptz not null default now()
);

create table if not exists public.logs (
    id bigserial primary key,
    level text not null,
    category text not null,
    message text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

-- Safe migration for users who ran an older schema.
alter table public.dubbing_tasks add column if not exists updated_at timestamptz not null default now();
alter table public.users add column if not exists updated_at timestamptz not null default now();
alter table public.users add column if not exists last_active_at timestamptz not null default now();

create index if not exists idx_users_telegram_user_id on public.users(telegram_user_id);
create index if not exists idx_users_last_active_at on public.users(last_active_at desc);
create index if not exists idx_dubbing_tasks_status on public.dubbing_tasks(status);
create index if not exists idx_dubbing_tasks_telegram_user_id on public.dubbing_tasks(telegram_user_id);
create index if not exists idx_dubbing_tasks_created_at on public.dubbing_tasks(created_at desc);
create index if not exists idx_dubbing_tasks_updated_at on public.dubbing_tasks(updated_at desc);
create index if not exists idx_logs_created_at on public.logs(created_at desc);

create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_users_updated_at on public.users;
create trigger trg_users_updated_at
before update on public.users
for each row execute function public.set_updated_at();

drop trigger if exists trg_dubbing_tasks_updated_at on public.dubbing_tasks;
create trigger trg_dubbing_tasks_updated_at
before update on public.dubbing_tasks
for each row execute function public.set_updated_at();

-- Recommended for server-side bots using SUPABASE_SERVICE_KEY.
-- Service role bypasses RLS. If you enable RLS, add policies carefully.

-- Force Supabase/PostgREST Data API to see newly added columns immediately.
notify pgrst, 'reload schema';
