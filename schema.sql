-- Kingdoms at War Activity Tracker — Database Schema
-- SQLite

-- A "person" is a real human in the clan/Discord. Every account (main or alt)
-- belongs to exactly one person. A person may or may not have a linked Discord ID.
CREATE TABLE IF NOT EXISTS people (
    person_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id    TEXT UNIQUE,              -- nullable; set via /register
    display_name  TEXT,                     -- optional friendly label, defaults to main account name
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- An "account" is an in-game name as it appears in EB screenshots.
-- Every account has exactly one owner (a person) and a role.
-- role = 'permanent_main' | 'hopper_main' | 'alt'
-- "hopper" = a non-permanent member who shows up in EBs without being on the roster.
-- For alts, owner_person_id points to whoever owns them (permanent or hopper).
CREATE TABLE IF NOT EXISTS accounts (
    account_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    game_name       TEXT NOT NULL UNIQUE,   -- exact in-game name, case-sensitive match
    owner_person_id INTEGER NOT NULL REFERENCES people(person_id),
    role            TEXT NOT NULL CHECK (role IN ('permanent_main', 'hopper_main', 'alt')),
    is_alt          INTEGER NOT NULL DEFAULT 0 CHECK (is_alt IN (0,1)),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_person_id);
CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role);

-- An Epic Battle event. One EB = one set of screenshots uploaded together.
CREATE TABLE IF NOT EXISTS epic_battles (
    eb_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    eb_label      TEXT,                     -- e.g. boss name from screenshot, optional
    eb_date       TEXT NOT NULL,             -- ISO date, supplied by admin at upload time
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per account per EB: how many successful actions ("hits") they got.
-- Absence of a row for an account in a given EB = did not participate.
CREATE TABLE IF NOT EXISTS participations (
    participation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    eb_id               INTEGER NOT NULL REFERENCES epic_battles(eb_id),
    account_id          INTEGER NOT NULL REFERENCES accounts(account_id),
    successful_actions  INTEGER NOT NULL,
    rank_in_eb          INTEGER,             -- as shown on leaderboard, for reference/debugging
    UNIQUE(eb_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_participations_eb ON participations(eb_id);
CREATE INDEX IF NOT EXISTS idx_participations_account ON participations(account_id);

-- Staging table: names extracted from a screenshot batch that didn't match
-- any known account. As of this version, unmatched names are auto-classified
-- as hoppers rather than sitting here unresolved. This table is now used
-- only when extraction itself is ambiguous (e.g. unreadable name), and stays
-- available for manual admin correction via /review_pending.
CREATE TABLE IF NOT EXISTS pending_review (
    pending_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    eb_id               INTEGER NOT NULL REFERENCES epic_battles(eb_id),
    extracted_name      TEXT NOT NULL,
    successful_actions  INTEGER NOT NULL,
    rank_in_eb          INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
