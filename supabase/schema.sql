-- ============================================================
-- Piemonte Wine Map - Supabase schema
-- Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
-- Safe to re-run (uses "if not exists" / "on conflict").
-- ============================================================

-- ---- Tables -------------------------------------------------

create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  display_name text not null,
  created_at   timestamptz default now()
);

create table if not exists public.producers (
  id         uuid primary key default gen_random_uuid(),
  name       text not null unique,
  commune    text,
  docg       text,
  lat        double precision,
  lon        double precision,
  website    text,
  created_by uuid references auth.users(id),
  created_at timestamptz default now()
);

create table if not exists public.wines (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  year        int,
  producer_id uuid not null references public.producers(id) on delete cascade,
  created_by  uuid references auth.users(id),
  created_at  timestamptz default now()
);

create table if not exists public.ratings (
  id         uuid primary key default gen_random_uuid(),
  wine_id    uuid not null references public.wines(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  score      int not null check (score >= 0 and score <= 10),
  created_at timestamptz default now(),
  unique (wine_id, user_id)        -- one rating per user per wine (upsert to change)
);

-- ---- Auto-create a profile row when a user signs up ---------

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, split_part(new.email, '@', 1))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---- Row-level security ------------------------------------
-- Everyone signed in can READ everything (so friends see each other's
-- wines and ratings). You can only WRITE rows that belong to you.

alter table public.profiles  enable row level security;
alter table public.producers enable row level security;
alter table public.wines     enable row level security;
alter table public.ratings   enable row level security;

-- profiles
create policy "profiles read"   on public.profiles for select to authenticated using (true);
create policy "profiles insert" on public.profiles for insert to authenticated with check (id = auth.uid());
create policy "profiles update" on public.profiles for update to authenticated using (id = auth.uid());

-- producers
create policy "producers read"   on public.producers for select to authenticated using (true);
create policy "producers insert" on public.producers for insert to authenticated with check (auth.uid() is not null);
create policy "producers update" on public.producers for update to authenticated using (created_by = auth.uid());
create policy "producers delete" on public.producers for delete to authenticated using (created_by = auth.uid());

-- wines
create policy "wines read"   on public.wines for select to authenticated using (true);
create policy "wines insert" on public.wines for insert to authenticated with check (auth.uid() is not null);
create policy "wines update" on public.wines for update to authenticated using (created_by = auth.uid());
create policy "wines delete" on public.wines for delete to authenticated using (created_by = auth.uid());

-- ratings
create policy "ratings read"   on public.ratings for select to authenticated using (true);
create policy "ratings insert" on public.ratings for insert to authenticated with check (user_id = auth.uid());
create policy "ratings update" on public.ratings for update to authenticated using (user_id = auth.uid());
create policy "ratings delete" on public.ratings for delete to authenticated using (user_id = auth.uid());
