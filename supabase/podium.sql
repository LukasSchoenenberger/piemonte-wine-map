-- ============================================================
-- Piemonte Wine Map - Podium (leaderboard)
-- Run once in the Supabase SQL Editor (after homebase.sql).
--
-- homebase / fridge_wines are private (per-user RLS). The podium needs every
-- user's totals, so we expose ONLY the aggregate ranking via a SECURITY
-- DEFINER function. It returns display name + star counts per category and
-- never reveals anyone's actual cellar contents.
-- ============================================================

create or replace function public.leaderboard()
returns table (
  display_name text,
  white  int,
  rose   int,
  red    int,
  fridge int
)
language sql
security definer
set search_path = public
as $$
  select
    p.display_name,
    coalesce(h.points_white, 0)              as white,
    coalesce(h.points_rose,  0)              as rose,
    coalesce(h.points_red,   0)              as red,
    coalesce(floor(fw.total / 50)::int, 0)   as fridge
  from public.profiles p
  left join public.homebase h on h.user_id = p.id
  left join (
    select user_id, sum(price) as total
    from public.fridge_wines
    group by user_id
  ) fw on fw.user_id = p.id;
$$;

-- Only logged-in users may read the leaderboard.
revoke all on function public.leaderboard() from public;
grant execute on function public.leaderboard() to authenticated;
