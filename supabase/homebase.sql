-- ============================================================
-- Piemonte Wine Map - Homebase feature
-- Run once in the Supabase SQL Editor (after schema.sql).
-- Per-user avatar points (by wine colour) + a personal fridge cellar.
-- ============================================================

create table if not exists public.homebase (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  points_white int not null default 0,
  points_rose  int not null default 0,
  points_red   int not null default 0
);

create table if not exists public.fridge_wines (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  wine_id    uuid not null references public.wines(id) on delete cascade,
  price      numeric not null check (price >= 0),
  created_at timestamptz default now()
);

alter table public.homebase     enable row level security;
alter table public.fridge_wines enable row level security;

-- Homebase + fridge are PRIVATE: each user only sees and writes their own.
create policy "homebase own select" on public.homebase
  for select to authenticated using (user_id = auth.uid());
create policy "homebase own insert" on public.homebase
  for insert to authenticated with check (user_id = auth.uid());
create policy "homebase own update" on public.homebase
  for update to authenticated using (user_id = auth.uid());

create policy "fridge own select" on public.fridge_wines
  for select to authenticated using (user_id = auth.uid());
create policy "fridge own insert" on public.fridge_wines
  for insert to authenticated with check (user_id = auth.uid());
create policy "fridge own delete" on public.fridge_wines
  for delete to authenticated using (user_id = auth.uid());
