-- supabase_schema.sql
--
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New query).
-- Use the service_role key in .env (Settings → API → service_role) —
-- it bypasses RLS so no policies are needed.
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / CREATE OR REPLACE.

-- ── tables ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    ts          DOUBLE PRECISION NOT NULL,
    anon_user   TEXT NOT NULL,
    anon_chat   TEXT NOT NULL,
    chat_type   TEXT NOT NULL,
    status      TEXT NOT NULL,
    video_bytes INTEGER DEFAULT 0,
    watermark   BOOLEAN,
    url         TEXT,
    reason      TEXT
);

-- Add columns to existing deployments that pre-date them.
ALTER TABLE events ADD COLUMN IF NOT EXISTS watermark BOOLEAN;
ALTER TABLE events ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS reason TEXT;

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_anon_user ON events(anon_user);

CREATE TABLE IF NOT EXISTS known_users (
    chat_id     BIGINT PRIMARY KEY,
    first_seen  DOUBLE PRECISION NOT NULL,
    last_seen   DOUBLE PRECISION NOT NULL
);

-- ── functions ────────────────────────────────────────────────────────

-- Upsert: insert new user or update last_seen (keeps first_seen intact).
CREATE OR REPLACE FUNCTION touch_user(p_chat_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO known_users (chat_id, first_seen, last_seen)
    VALUES (p_chat_id, extract(epoch FROM now()), extract(epoch FROM now()))
    ON CONFLICT (chat_id)
    DO UPDATE SET last_seen = extract(epoch FROM now());
END;
$$;

-- Return the last N failed events (for /debug admin command).
CREATE OR REPLACE FUNCTION get_recent_failures(p_limit INTEGER DEFAULT 10)
RETURNS JSON
LANGUAGE plpgsql AS $$
DECLARE
    result JSON;
BEGIN
    SELECT COALESCE(json_agg(t), '[]'::json) INTO result FROM (
        SELECT ts, status, chat_type, url, reason, anon_user
        FROM events
        WHERE status <> 'ok'
        ORDER BY ts DESC
        LIMIT p_limit
    ) t;
    RETURN result;
END;
$$;

-- Aggregated stats returned as a single JSON object.
CREATE OR REPLACE FUNCTION get_stats()
RETURNS JSON
LANGUAGE plpgsql AS $$
DECLARE
    result JSON;
BEGIN
    SELECT json_build_object(
        'total_requests',   (SELECT COUNT(*) FROM events),
        'successful',       (SELECT COUNT(*) FROM events WHERE status = 'ok'),
        'failed',           (SELECT COUNT(*) FROM events WHERE status <> 'ok'),
        'unique_users',     (SELECT COUNT(DISTINCT anon_user) FROM events),
        'unique_chats',     (SELECT COUNT(DISTINCT anon_chat) FROM events),
        'total_video_bytes', COALESCE(
            (SELECT SUM(video_bytes) FROM events WHERE status = 'ok'), 0
        ),
        'watermark_yes', (SELECT COUNT(*) FROM events WHERE watermark = TRUE),
        'watermark_no',  (SELECT COUNT(*) FROM events WHERE watermark = FALSE),
        'top_users', COALESCE((
            SELECT json_agg(t) FROM (
                SELECT anon_user AS anon_id,
                       COUNT(*)  AS requests,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS success
                FROM events
                GROUP BY anon_user
                ORDER BY requests DESC
                LIMIT 10
            ) t
        ), '[]'::json),
        'daily_last_7d', COALESCE((
            SELECT json_agg(t) FROM (
                SELECT to_char(to_timestamp(ts), 'YYYY-MM-DD') AS date,
                       COUNT(*)  AS requests,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS success
                FROM events
                WHERE ts >= extract(epoch FROM now()) - 7 * 86400
                GROUP BY date
                ORDER BY date
            ) t
        ), '[]'::json)
    ) INTO result;
    RETURN result;
END;
$$;
