// -------------------------------------------------------
// Supabase data layer: auth + producers + wines + ratings.
// All functions return plain data (or throw) so the UI stays thin.
// -------------------------------------------------------
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { SUPABASE_URL, SUPABASE_ANON_KEY } from './config.js';

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// -------------------------------------------------------
// Auth
// -------------------------------------------------------
export async function getSession() {
  const { data } = await supabase.auth.getSession();
  return data.session;
}

export async function signIn(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

export async function signOut() {
  await supabase.auth.signOut();
}

// Re-emit Supabase auth changes as a DOM event the UI can listen for.
// Fires on sign-in, sign-out, and silent token refresh.
supabase.auth.onAuthStateChange((event, session) => {
  window.dispatchEvent(new CustomEvent('auth-change', { detail: { event, session } }));
});

export async function getMyUserId() {
  const { data } = await supabase.auth.getUser();
  return data.user?.id ?? null;
}

export async function getMyDisplayName() {
  const uid = await getMyUserId();
  if (!uid) return null;
  const { data } = await supabase
    .from('profiles').select('display_name').eq('id', uid).maybeSingle();
  return data?.display_name ?? null;
}

// -------------------------------------------------------
// Producers
// -------------------------------------------------------
export async function listProducers() {
  const { data, error } = await supabase
    .from('producers')
    .select('id, name, commune, docg, lat, lon, website')
    .order('name');
  if (error) throw error;
  return data;
}

export async function addProducer({ name, commune, docg, lat, lon, website }) {
  const uid = await getMyUserId();
  const { data, error } = await supabase
    .from('producers')
    .insert({
      name,
      commune: commune || null,
      docg: docg || null,
      lat: lat ?? null,
      lon: lon ?? null,
      website: website || null,
      created_by: uid,
    })
    .select()
    .single();
  if (error) throw error;
  return data;
}

// -------------------------------------------------------
// Wines
// -------------------------------------------------------
export async function listWines() {
  // Each wine with its producer and the list of scores (for averages).
  const { data, error } = await supabase
    .from('wines')
    .select('id, name, year, producer_id, producers(name), ratings(score)')
    .order('name');
  if (error) throw error;
  return data;
}

export async function listWinesByProducer(producerId) {
  const { data, error } = await supabase
    .from('wines')
    .select('id, name, year, ratings(score)')
    .eq('producer_id', producerId)
    .order('year', { ascending: false });
  if (error) throw error;
  return data;
}

export async function addWine({ name, year, producerId }) {
  const uid = await getMyUserId();
  const { data, error } = await supabase
    .from('wines')
    .insert({
      name,
      year: year ?? null,
      producer_id: producerId,
      created_by: uid,
    })
    .select()
    .single();
  if (error) throw error;
  return data;
}

// -------------------------------------------------------
// Ratings
// -------------------------------------------------------
export async function listRatingsForWine(wineId) {
  // Two queries: ratings -> profiles. There's no direct FK from ratings to
  // profiles (both reference auth.users), so PostgREST can't embed profiles
  // directly. We join on user_id in JS instead.
  const { data: ratings, error } = await supabase
    .from('ratings')
    .select('score, user_id')
    .eq('wine_id', wineId);
  if (error) throw error;
  if (!ratings.length) return [];

  const ids = [...new Set(ratings.map((r) => r.user_id))];
  const { data: profiles } = await supabase
    .from('profiles')
    .select('id, display_name')
    .in('id', ids);
  const nameById = Object.fromEntries((profiles ?? []).map((p) => [p.id, p.display_name]));

  return ratings.map((r) => ({
    score: r.score,
    user_id: r.user_id,
    name: nameById[r.user_id] ?? r.user_id.slice(0, 8),
  }));
}

export async function myRatingForWine(wineId) {
  const uid = await getMyUserId();
  if (!uid) return null;
  const { data } = await supabase
    .from('ratings').select('score').eq('wine_id', wineId).eq('user_id', uid).maybeSingle();
  return data?.score ?? null;
}

export async function upsertRating(wineId, score) {
  const uid = await getMyUserId();
  const { error } = await supabase
    .from('ratings')
    .upsert({ wine_id: wineId, user_id: uid, score }, { onConflict: 'wine_id,user_id' });
  if (error) throw error;
}
