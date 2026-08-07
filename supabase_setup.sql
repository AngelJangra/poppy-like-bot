-- ============================================================
-- Free Fire Like Bot - Supabase Setup
-- 👑 Owner & Developer: @YourPOPPY42
-- 🇮🇳 Indian-Only Free Fire Like Bot
-- Run this SQL in Supabase Dashboard > SQL Editor
-- ============================================================

-- Table: FF accounts stored via /addaccounts
CREATE TABLE IF NOT EXISTS ff_accounts (
  id BIGSERIAL PRIMARY KEY,
  uid TEXT NOT NULL,
  password TEXT NOT NULL,
  acc_id TEXT DEFAULT '',
  name TEXT DEFAULT '',
  region TEXT NOT NULL DEFAULT 'IND',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Table: Cached JWT tokens
CREATE TABLE IF NOT EXISTS ff_tokens (
  id BIGSERIAL PRIMARY KEY,
  uid TEXT NOT NULL,
  token TEXT DEFAULT '',
  region TEXT DEFAULT '',
  access_token TEXT DEFAULT '',
  open_id TEXT DEFAULT '',
  fetched_at BIGINT DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for fast UID lookups
CREATE INDEX IF NOT EXISTS idx_ff_accounts_uid ON ff_accounts (uid);
CREATE INDEX IF NOT EXISTS idx_ff_accounts_region ON ff_accounts (region);
CREATE INDEX IF NOT EXISTS idx_ff_tokens_uid ON ff_tokens (uid);

-- ============================================================
-- SECURITY: Restrict access
-- The bot uses the service_role key (bypasses RLS).
-- Disable RLS for these tables so the bot service role can read/write.
-- NOTE: The service_role key bypasses RLS anyway, but for safety
-- we cap API access to authenticated only if you use anon key instead.
-- ============================================================
ALTER TABLE ff_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ff_tokens ENABLE ROW LEVEL SECURITY;

-- Optional: allow all access via anon key (only if not using service_role)
-- CREATE POLICY "Allow all on ff_accounts" ON ff_accounts FOR ALL USING (true) WITH CHECK (true);
-- CREATE POLICY "Allow all on ff_tokens" ON ff_tokens FOR ALL USING (true) WITH CHECK (true);