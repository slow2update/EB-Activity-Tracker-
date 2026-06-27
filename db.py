"""
db.py — all database access for the activity tracker bot.
SQLite, single file, no external DB server needed.
"""
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "data/activity.db")


def init_db():
    """Create tables if they don't exist. Safe to call every startup."""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with get_conn() as conn:
        with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
            conn.executescript(f.read())
    _migrate_guest_to_hopper()


def _migrate_guest_to_hopper():
    """
    One-time migration: older deployments used role='guest_main'. The CHECK
    constraint only allows 'permanent_main', 'hopper_main', 'alt' now, so any
    leftover 'guest_main' rows need rewriting. SQLite can't alter a CHECK
    constraint in place, so we rebuild the accounts table if old rows exist.
    Safe to run on every startup -- it's a no-op once migrated.
    """
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            old_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM accounts WHERE role = 'guest_main'"
            ).fetchone()
        except sqlite3.OperationalError:
            return  # accounts table doesn't exist yet, nothing to migrate
        if old_rows["c"] == 0:
            return

        conn.executescript("""
            ALTER TABLE accounts RENAME TO accounts_old_migration;

            CREATE TABLE accounts (
                account_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name       TEXT NOT NULL UNIQUE,
                owner_person_id INTEGER NOT NULL REFERENCES people(person_id),
                role            TEXT NOT NULL CHECK (role IN ('permanent_main', 'hopper_main', 'alt')),
                is_alt          INTEGER NOT NULL DEFAULT 0 CHECK (is_alt IN (0,1)),
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            INSERT INTO accounts SELECT
                account_id, game_name, owner_person_id,
                CASE WHEN role = 'guest_main' THEN 'hopper_main' ELSE role END,
                is_alt, created_at
            FROM accounts_old_migration;

            DROP TABLE accounts_old_migration;

            CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_person_id);
            CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role);
        """)
        conn.execute("PRAGMA foreign_keys = ON")
    print(f"[migration] Converted {old_rows['c']} 'guest_main' account(s) to 'hopper_main'.")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# People & accounts
# ---------------------------------------------------------------------------

def get_or_create_person_by_discord(discord_id: str, display_name: str = None) -> int:
    """Return person_id for a discord user, creating a person row if needed."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT person_id FROM people WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        if row:
            return row["person_id"]
        cur = conn.execute(
            "INSERT INTO people (discord_id, display_name) VALUES (?, ?)",
            (discord_id, display_name),
        )
        return cur.lastrowid


def get_account_by_name(game_name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM accounts WHERE game_name = ?", (game_name,)
        ).fetchone()


def get_person_main_account(person_id: int):
    """A person's main account is the one with role permanent_main or hopper_main."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM accounts
               WHERE owner_person_id = ? AND role IN ('permanent_main', 'hopper_main')""",
            (person_id,),
        ).fetchone()


def register_main_account(discord_id: str, game_name: str, as_permanent: bool = True):
    """
    /register — link a discord user's identity to their in-game main name.
    Creates the person if needed. If the account name already exists and is
    unowned-by-discord (e.g. created earlier from a screenshot as a hopper),
    this just attaches the discord_id to that existing person.
    Returns (success: bool, message: str).
    """
    existing_account = get_account_by_name(game_name)
    person_id = get_or_create_person_by_discord(discord_id)

    if existing_account:
        if existing_account["owner_person_id"] != person_id:
            # Account exists under a different (likely discord-less) person record.
            # Re-point that person's discord_id to this caller, merging identity.
            with get_conn() as conn:
                conn.execute(
                    "UPDATE people SET discord_id = ? WHERE person_id = ?",
                    (discord_id, existing_account["owner_person_id"]),
                )
            return True, f"Linked your Discord account to existing game name '{game_name}'."
        return True, f"You're already registered as '{game_name}'."

    role = "permanent_main" if as_permanent else "hopper_main"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (game_name, owner_person_id, role, is_alt) VALUES (?, ?, ?, 0)",
            (game_name, person_id, role),
        )
    return True, f"Registered '{game_name}' as your main account ({role.replace('_', ' ')})."


def add_alt_for_discord_user(discord_id: str, alt_game_name: str):
    """
    /addalt — add an alt under the caller's own main account.
    Does NOT change the caller's role — a hopper adding an alt stays a
    hopper. Promotion to permanent only happens via /addmember or /roster add.
    """
    with get_conn() as conn:
        person = conn.execute(
            "SELECT person_id FROM people WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    if not person:
        return False, "You haven't registered yet. Use `/register <your_main_game_name>` first."

    person_id = person["person_id"]
    main = get_person_main_account(person_id)
    if not main:
        return False, "You haven't registered a main account yet. Use `/register <your_main_game_name>` first."

    existing = get_account_by_name(alt_game_name)
    if existing:
        return False, f"'{alt_game_name}' is already registered (owner: account exists)."

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (game_name, owner_person_id, role, is_alt) VALUES (?, ?, 'alt', 1)",
            (alt_game_name, person_id),
        )
    return True, f"Added '{alt_game_name}' as your alt, linked to '{main['game_name']}'."


def add_permanent_member(game_name: str):
    """/addmember or /roster add — add a new permanent main account, no discord link required."""
    existing = get_account_by_name(game_name)
    if existing:
        return False, f"'{game_name}' is already registered as {existing['role'].replace('_', ' ')}."
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO people (display_name) VALUES (?)", (game_name,))
        person_id = cur.lastrowid
        conn.execute(
            "INSERT INTO accounts (game_name, owner_person_id, role, is_alt) VALUES (?, ?, 'permanent_main', 0)",
            (game_name, person_id),
        )
    return True, f"Added '{game_name}' to the permanent roster."


def link_alt(alt_name: str, owner_name: str):
    """
    Admin override: /alt link <alt> <owner>. Owner can be permanent or hopper main.
    Linking an alt does NOT change the owner's role — a hopper with a linked
    alt stays a hopper. Promotion to permanent only happens via /addmember
    or /roster add, never as a side effect of alt-linking.
    """
    owner_account = get_account_by_name(owner_name)
    if not owner_account:
        return False, f"Owner account '{owner_name}' not found. Add them first."
    if owner_account["is_alt"]:
        return False, f"'{owner_name}' is itself an alt — link to its main account instead."

    existing_alt = get_account_by_name(alt_name)
    with get_conn() as conn:
        if existing_alt:
            conn.execute(
                "UPDATE accounts SET owner_person_id = ?, role = 'alt', is_alt = 1 WHERE account_id = ?",
                (owner_account["owner_person_id"], existing_alt["account_id"]),
            )
        else:
            conn.execute(
                "INSERT INTO accounts (game_name, owner_person_id, role, is_alt) VALUES (?, ?, 'alt', 1)",
                (alt_name, owner_account["owner_person_id"]),
            )
    return True, f"Linked '{alt_name}' as an alt of '{owner_name}'."


def unlink_alt(alt_name: str):
    account = get_account_by_name(alt_name)
    if not account or not account["is_alt"]:
        return False, f"'{alt_name}' is not currently registered as an alt."
    with get_conn() as conn:
        # Unlinking demotes the alt itself to its own hopper_main under a
        # fresh person record. The former owner's role is untouched — alt
        # linking/unlinking never affects whether someone is permanent or
        # hopper; that's only ever set via /addmember, /roster add, or this
        # unlink (which only affects the alt, not the owner).
        cur = conn.execute("INSERT INTO people (display_name) VALUES (?)", (alt_name,))
        new_person_id = cur.lastrowid
        conn.execute(
            "UPDATE accounts SET owner_person_id = ?, role = 'hopper_main', is_alt = 0 WHERE account_id = ?",
            (new_person_id, account["account_id"]),
        )
    return True, f"Unlinked '{alt_name}'. It's now tracked as its own hopper account."


def list_alts(owner_name: str):
    owner_account = get_account_by_name(owner_name)
    if not owner_account:
        return None
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT game_name FROM accounts
               WHERE owner_person_id = ? AND role = 'alt'""",
            (owner_account["owner_person_id"],),
        ).fetchall()
    return [r["game_name"] for r in rows]


def get_or_create_hopper_account(game_name: str):
    """
    Returns the account row for game_name, creating it as a fresh hopper_main
    if it doesn't exist yet. Used during screenshot processing: any name that
    doesn't match an existing account is automatically classified as a hopper
    rather than left in limbo. Exact-match only — this never fuzzy-matches a
    near-identical name to an existing account.
    """
    existing = get_account_by_name(game_name)
    if existing:
        return existing
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO people (display_name) VALUES (?)", (game_name,))
        person_id = cur.lastrowid
        conn.execute(
            "INSERT INTO accounts (game_name, owner_person_id, role, is_alt) VALUES (?, ?, 'hopper_main', 0)",
            (game_name, person_id),
        )
    return get_account_by_name(game_name)


# ---------------------------------------------------------------------------
# Epic Battles & participation
# ---------------------------------------------------------------------------

def create_eb(eb_date: str, eb_label: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO epic_battles (eb_label, eb_date) VALUES (?, ?)",
            (eb_label, eb_date),
        )
        return cur.lastrowid


def record_participation(eb_id: int, account_id: int, successful_actions: int, rank_in_eb: int = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO participations (eb_id, account_id, successful_actions, rank_in_eb)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(eb_id, account_id) DO UPDATE SET
                 successful_actions = excluded.successful_actions,
                 rank_in_eb = excluded.rank_in_eb""",
            (eb_id, account_id, successful_actions, rank_in_eb),
        )


def add_pending_review(eb_id: int, extracted_name: str, successful_actions: int, rank_in_eb: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_review (eb_id, extracted_name, successful_actions, rank_in_eb)
               VALUES (?, ?, ?, ?)""",
            (eb_id, extracted_name, successful_actions, rank_in_eb),
        )
        return cur.lastrowid


def get_pending_reviews(eb_id: int = None):
    with get_conn() as conn:
        if eb_id:
            return conn.execute(
                "SELECT * FROM pending_review WHERE status = 'pending' AND eb_id = ?", (eb_id,)
            ).fetchall()
        return conn.execute("SELECT * FROM pending_review WHERE status = 'pending'").fetchall()


def resolve_pending_as_hopper(pending_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pending_review WHERE pending_id = ?", (pending_id,)).fetchone()
        if not row:
            return False, "Pending entry not found."
    account = get_or_create_hopper_account(row["extracted_name"])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO participations (eb_id, account_id, successful_actions, rank_in_eb) VALUES (?, ?, ?, ?)",
            (row["eb_id"], account["account_id"], row["successful_actions"], row["rank_in_eb"]),
        )
        conn.execute("UPDATE pending_review SET status = 'resolved' WHERE pending_id = ?", (pending_id,))
    return True, f"Added '{row['extracted_name']}' as a hopper and recorded their participation."


def resolve_pending_as_alt(pending_id: int, owner_name: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pending_review WHERE pending_id = ?", (pending_id,)).fetchone()
        if not row:
            return False, "Pending entry not found."
    ok, msg = link_alt(row["extracted_name"], owner_name)
    if not ok:
        return ok, msg
    account = get_account_by_name(row["extracted_name"])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO participations (eb_id, account_id, successful_actions, rank_in_eb) VALUES (?, ?, ?, ?)",
            (row["eb_id"], account["account_id"], row["successful_actions"], row["rank_in_eb"]),
        )
        conn.execute("UPDATE pending_review SET status = 'resolved' WHERE pending_id = ?", (pending_id,))
    return True, f"Linked '{row['extracted_name']}' as alt of '{owner_name}' and recorded participation."


def resolve_pending_as_correction(pending_id: int, correct_name: str):
    """The extracted name was a misread; correct_name is the real existing account."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pending_review WHERE pending_id = ?", (pending_id,)).fetchone()
        if not row:
            return False, "Pending entry not found."
    account = get_account_by_name(correct_name)
    if not account:
        return False, f"'{correct_name}' doesn't exist as an account."
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO participations (eb_id, account_id, successful_actions, rank_in_eb)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(eb_id, account_id) DO UPDATE SET
                 successful_actions = excluded.successful_actions""",
            (row["eb_id"], account["account_id"], row["successful_actions"], row["rank_in_eb"]),
        )
        conn.execute("UPDATE pending_review SET status = 'resolved' WHERE pending_id = ?", (pending_id,))
    return True, f"Recorded participation for '{correct_name}' (corrected from misread '{row['extracted_name']}')."


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_activity_report(days: int):
    """
    Returns dict with three alphabetically-sorted lists: permanent, hopper, alt.
    Each entry: game_name, ebs_attended, ebs_total, avg_actions, owner_name (alts only).
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    with get_conn() as conn:
        total_ebs = conn.execute(
            "SELECT COUNT(*) AS c FROM epic_battles WHERE eb_date >= ?", (cutoff,)
        ).fetchone()["c"]

        rows = conn.execute(
            """
            SELECT a.game_name, a.role, a.is_alt,
                   COUNT(pa.participation_id) AS ebs_attended,
                   COALESCE(AVG(pa.successful_actions), 0) AS avg_actions,
                   owner_acct.game_name AS owner_name
            FROM accounts a
            LEFT JOIN participations pa
                   ON pa.account_id = a.account_id
                  AND pa.eb_id IN (SELECT eb_id FROM epic_battles WHERE eb_date >= ?)
            LEFT JOIN accounts owner_acct
                   ON a.is_alt = 1 AND owner_acct.owner_person_id = a.owner_person_id
                  AND owner_acct.role IN ('permanent_main', 'hopper_main')
            GROUP BY a.account_id
            ORDER BY a.game_name COLLATE NOCASE ASC
            """,
            (cutoff,),
        ).fetchall()

    permanent, hopper, alt = [], [], []
    for r in rows:
        entry = {
            "game_name": r["game_name"],
            "ebs_attended": r["ebs_attended"],
            "ebs_total": total_ebs,
            "avg_actions": round(r["avg_actions"], 1),
        }
        if r["role"] == "permanent_main":
            permanent.append(entry)
        elif r["role"] == "hopper_main":
            hopper.append(entry)
        else:
            entry["owner_name"] = r["owner_name"]
            alt.append(entry)

    return {"total_ebs": total_ebs, "permanent": permanent, "hopper": hopper, "alt": alt}
