-- Hotfix migration for PGRST204:
-- "Could not find the 'updated_at' column of 'dubbing_tasks' in the schema cache"
-- Run this file in Supabase SQL Editor, then redeploy Render.

alter table public.dubbing_tasks
    add column if not exists updated_at timestamptz not null default now();

alter table public.users
    add column if not exists updated_at timestamptz not null default now();

alter table public.users
    add column if not exists last_active_at timestamptz not null default now();

create index if not exists idx_dubbing_tasks_updated_at
    on public.dubbing_tasks(updated_at desc);

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

-- Force Supabase/PostgREST to refresh its schema cache after ALTER TABLE.
notify pgrst, 'reload schema';

-- Verify column exists.
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name in ('users', 'dubbing_tasks')
  and column_name in ('updated_at', 'last_active_at')
order by table_name, column_name;
