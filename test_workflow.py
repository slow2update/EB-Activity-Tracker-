"""
test_workflow.py — exercises db.py end-to-end with data shaped like the
real screenshots, without needing Discord or a live Anthropic API key.
Run with: python3 test_workflow.py
"""
import os
os.environ["DB_PATH"] = "data/test_activity.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import db

db.init_db()

# --- Simulate roster setup ---
print("=== Setting up permanent roster ===")
for name in ["Barney", "WolverineJ", "Forseti", "Pherret"]:
    ok, msg = db.add_permanent_member(name)
    print(msg)

print("\n=== Self-service registration (simulating /register) ===")
ok, msg = db.register_main_account("discord_user_1", "-ALCH3MY-")
print(msg)

print("\n=== Adding an alt (simulating /addalt) ===")
ok, msg = db.add_alt_for_discord_user("discord_user_1", "-ALCH3MY-D3M0N-")
print(msg)

print("\n=== Admin linking an alt manually (simulating /alt link) ===")
ok, msg = db.link_alt("BarneyJr", "Barney")
print(msg)

# --- Simulate two EBs worth of extracted screenshot data ---
print("\n=== Recording EB #1 ===")
eb1 = db.create_eb("2026-06-20", "Neidria of the Hoarfrost")
extracted_eb1 = [
    {"rank": 19, "name": "Slycvmmings", "successful_actions": 60},   # unrecognized -> pending
    {"rank": 20, "name": "-ALCH3MY-D3M0N-", "successful_actions": 272},  # matches alt
    {"rank": 24, "name": "-ALCH3MY-", "successful_actions": 274},    # matches main
    {"rank": 22, "name": "Barney", "successful_actions": 50},
    {"rank": 38, "name": "WolverineJ", "successful_actions": 126},
]
for e in extracted_eb1:
    acct = db.get_account_by_name(e["name"])
    if acct:
        db.record_participation(eb1, acct["account_id"], e["successful_actions"], e["rank"])
        print(f"  Matched: {e['name']} -> recorded")
    else:
        pid = db.add_pending_review(eb1, e["name"], e["successful_actions"], e["rank"])
        print(f"  Unmatched: {e['name']} -> pending_id {pid}")

print("\n=== Recording EB #2 ===")
eb2 = db.create_eb("2026-06-23", "Neidria of the Hoarfrost")
extracted_eb2 = [
    {"rank": 5, "name": "Barney", "successful_actions": 80},
    {"rank": 10, "name": "WolverineJ", "successful_actions": 140},
    {"rank": 15, "name": "-ALCH3MY-", "successful_actions": 300},
    {"rank": 16, "name": "BarneyJr", "successful_actions": 22},
]
for e in extracted_eb2:
    acct = db.get_account_by_name(e["name"])
    if acct:
        db.record_participation(eb2, acct["account_id"], e["successful_actions"], e["rank"])
        print(f"  Matched: {e['name']} -> recorded")
    else:
        pid = db.add_pending_review(eb2, e["name"], e["successful_actions"], e["rank"])
        print(f"  Unmatched: {e['name']} -> pending_id {pid}")

# --- Resolve the pending unmatched name as a guest ---
print("\n=== Resolving pending reviews ===")
pending = db.get_pending_reviews()
for p in pending:
    print(f"Pending: #{p['pending_id']} {p['extracted_name']} ({p['successful_actions']} actions)")
    ok, msg = db.resolve_pending_as_guest(p["pending_id"])
    print(" ->", msg)

# --- Generate report ---
print("\n=== Activity Report (last 14 days) ===")
report = db.build_activity_report(14)
print(f"Total EBs in window: {report['total_ebs']}")
print("\nPERMANENT:")
for e in report["permanent"]:
    print(f"  {e['game_name']} — {e['ebs_attended']}/{e['ebs_total']} EBs, avg {e['avg_actions']} actions")
print("\nGUEST:")
for e in report["guest"]:
    print(f"  {e['game_name']} — {e['ebs_attended']}/{e['ebs_total']} EBs, avg {e['avg_actions']} actions")
print("\nALT:")
for e in report["alt"]:
    print(f"  {e['game_name']} (owner: {e['owner_name']}) — {e['ebs_attended']}/{e['ebs_total']} EBs, avg {e['avg_actions']} actions")

print("\n=== Sanity checks ===")
assert any(e["game_name"] == "Barney" and e["ebs_attended"] == 2 for e in report["permanent"]), "Barney should have 2 EBs"
assert any(e["game_name"] == "Slycvmmings" for e in report["guest"]), "Slycvmmings should be a guest"
assert any(e["game_name"] == "BarneyJr" and e["owner_name"] == "Barney" for e in report["alt"]), "BarneyJr should be alt of Barney"
assert any(e["game_name"] == "-ALCH3MY-D3M0N-" for e in report["alt"]), "-ALCH3MY-D3M0N- should be alt"
# Pherret and Forseti are permanent but had 0 participations -- should still show with 0
assert any(e["game_name"] == "Pherret" and e["ebs_attended"] == 0 for e in report["permanent"]), "Pherret should show 0 attendance"

print("\nALL ASSERTIONS PASSED ✅")
