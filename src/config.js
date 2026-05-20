// Supabase connection. The anon key is safe to expose in client code —
// row-level security policies (defined in supabase/schema.sql) are what
// protect the data, not secrecy of this key. Never put the service_role key here.
export const SUPABASE_URL = 'https://sibxzywebzkgrsnfjvgu.supabase.co';
export const SUPABASE_ANON_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNpYnh6eXdlYnprZ3JzbmZqdmd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkyODY4MzEsImV4cCI6MjA5NDg2MjgzMX0.V1QRFe_YEk4dSpD5Uvm80GsVXqqNI_Iq7eOEqhH2Tig';
