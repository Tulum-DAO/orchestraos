-- Push relay state (Cloudflare D1). Holds no card text, no names, no gateway URLs.

-- Single-use App Attest challenges, 5 minutes.
CREATE TABLE IF NOT EXISTS challenges (
  challenge  TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);

-- One row per attested app install key. `handle` is what the gateway knows; the APNs token
-- never leaves this table.
CREATE TABLE IF NOT EXISTS devices (
  handle      TEXT PRIMARY KEY,
  key_id      TEXT NOT NULL UNIQUE,
  public_key  TEXT NOT NULL,          -- SPKI, base64
  counter     INTEGER NOT NULL,       -- App Attest assertion counter
  apns_token  TEXT NOT NULL,
  env         TEXT NOT NULL,          -- sandbox | production (fixed by the attestation's aaguid)
  bundle_id   TEXT NOT NULL,
  platform    TEXT NOT NULL,
  install_id  TEXT,                   -- the gateway install this device bound itself to
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS devices_install ON devices (install_id);

-- One row per gateway install. `secret` keys the HMAC on its sends.
CREATE TABLE IF NOT EXISTS installs (
  install_id TEXT PRIMARY KEY,
  secret     TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- Fixed-window rate-limit counters: key = "<kind>:<id>:<window start>".
CREATE TABLE IF NOT EXISTS counters (
  k          TEXT PRIMARY KEY,
  n          INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
