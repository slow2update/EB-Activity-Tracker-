"""
bot.py — Kingdoms at War activity tracker Discord bot.

Setup:
  1. pip install -r requirements.txt
  2. Set environment variables: DISCORD_TOKEN, ANTHROPIC_API_KEY
  3. python bot.py

Slash commands are documented inline below each handler.
"""
import os
import io
import discord
from discord import app_commands
from discord.ext import commands

import db
import extraction

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Tracks in-flight screenshot batches per channel, keyed by channel_id,
# so an admin can post several images then run /process_eb once.
PENDING_BATCHES: dict[int, list[str]] = {}
UPLOAD_DIR = "data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@bot.event
async def on_ready():
    db.init_db()
    await bot.tree.sync()
    print(f"Logged in as {bot.user}. Slash commands synced.")


# ---------------------------------------------------------------------------
# Self-service commands — open to all members
# ---------------------------------------------------------------------------

@bot.tree.command(name="register", description="Link your Discord account to your in-game main account name.")
@app_commands.describe(game_name="Your exact in-game player name")
async def register(interaction: discord.Interaction, game_name: str):
    ok, msg = db.register_main_account(str(interaction.user.id), game_name)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="addalt", description="Register an alt account under your own main account.")
@app_commands.describe(alt_game_name="The exact in-game name of your alt account")
async def addalt(interaction: discord.Interaction, alt_game_name: str):
    ok, msg = db.add_alt_for_discord_user(str(interaction.user.id), alt_game_name)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="addmember", description="Add a new permanent clan member by their in-game name.")
@app_commands.describe(game_name="The exact in-game player name to add to the permanent roster")
async def addmember(interaction: discord.Interaction, game_name: str):
    ok, msg = db.add_permanent_member(game_name)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="bulk_addmember", description="Add multiple permanent members at once, one name per line.")
@app_commands.describe(names="Paste names, one per line (or comma-separated)")
async def bulk_addmember(interaction: discord.Interaction, names: str):
    # Split on newlines first, then also allow commas, in case someone pastes
    # a comma-separated list instead of one-per-line.
    raw_lines = names.replace(",", "\n").splitlines()
    candidates = [line.strip() for line in raw_lines if line.strip()]

    if not candidates:
        await interaction.response.send_message("No names found in that input.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    added, skipped = [], []
    for name in candidates:
        ok, msg = db.add_permanent_member(name)
        (added if ok else skipped).append(name)

    summary = f"**Bulk add complete** — {len(added)} added, {len(skipped)} skipped (already existed).\n"
    if added:
        summary += "\n**Added:**\n" + ", ".join(added)
    if skipped:
        summary += "\n\n**Skipped (already registered):**\n" + ", ".join(skipped)

    if len(summary) > 1900:
        buf = io.BytesIO(summary.encode("utf-8"))
        await interaction.followup.send(
            "Bulk add report attached (too long for inline message):",
            file=discord.File(buf, filename="bulk_add_report.txt"),
        )
    else:
        await interaction.followup.send(summary)


# ---------------------------------------------------------------------------
# Admin-style commands — open to all per current settings, kept as separate
# group for clarity and in case you want to restrict later with @app_commands.checks
# ---------------------------------------------------------------------------

alt_group = app_commands.Group(name="alt", description="Manage alt account links")


@alt_group.command(name="link", description="Link an alt account to its owner's main account.")
@app_commands.describe(alt_name="The alt's in-game name", owner_name="The owner's main in-game name")
async def alt_link(interaction: discord.Interaction, alt_name: str, owner_name: str):
    ok, msg = db.link_alt(alt_name, owner_name)
    await interaction.response.send_message(msg, ephemeral=True)


@alt_group.command(name="unlink", description="Unlink an alt account from its owner.")
@app_commands.describe(alt_name="The alt's in-game name")
async def alt_unlink(interaction: discord.Interaction, alt_name: str):
    ok, msg = db.unlink_alt(alt_name)
    await interaction.response.send_message(msg, ephemeral=True)


@alt_group.command(name="list", description="List all alts linked to a given owner.")
@app_commands.describe(owner_name="The owner's main in-game name")
async def alt_list(interaction: discord.Interaction, owner_name: str):
    alts = db.list_alts(owner_name)
    if alts is None:
        await interaction.response.send_message(f"'{owner_name}' not found.", ephemeral=True)
    elif not alts:
        await interaction.response.send_message(f"'{owner_name}' has no linked alts.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"Alts linked to '{owner_name}': {', '.join(alts)}", ephemeral=True
        )


bot.tree.add_command(alt_group)


# ---------------------------------------------------------------------------
# Screenshot ingestion
# ---------------------------------------------------------------------------

@bot.tree.command(name="start_eb_upload", description="Start a new EB screenshot batch. Upload images right after this, then run /process_eb.")
@app_commands.describe(eb_date="Date of this EB, format YYYY-MM-DD", eb_label="Optional label, e.g. boss name")
async def start_eb_upload(interaction: discord.Interaction, eb_date: str, eb_label: str = None):
    PENDING_BATCHES[interaction.channel_id] = {"eb_date": eb_date, "eb_label": eb_label, "paths": []}
    await interaction.response.send_message(
        f"Started EB batch for {eb_date}. Now upload your screenshots in this channel (as attachments), "
        f"then run `/process_eb` when done.",
        ephemeral=False,
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    batch = PENDING_BATCHES.get(message.channel.id)
    if batch and message.attachments:
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                local_path = os.path.join(UPLOAD_DIR, f"{message.id}_{attachment.filename}")
                await attachment.save(local_path)
                batch["paths"].append(local_path)
        await message.add_reaction("✅")
    await bot.process_commands(message)


@bot.tree.command(name="process_eb", description="Process all screenshots uploaded since /start_eb_upload in this channel.")
async def process_eb(interaction: discord.Interaction):
    batch = PENDING_BATCHES.get(interaction.channel_id)
    if not batch or not batch["paths"]:
        await interaction.response.send_message(
            "No pending screenshots found. Run `/start_eb_upload` first, then post images.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Processing {len(batch['paths'])} screenshot(s)... this may take a minute."
    )

    entries, errors = extraction.extract_from_batch(batch["paths"])
    eb_id = db.create_eb(batch["eb_date"], batch["eb_label"])

    matched, unmatched = 0, 0
    for entry in entries:
        name = entry.get("name")
        actions = entry.get("successful_actions")
        rank = entry.get("rank")
        if name is None or actions is None:
            continue
        account = db.get_account_by_name(name)
        if account:
            db.record_participation(eb_id, account["account_id"], actions, rank)
            matched += 1
        else:
            db.add_pending_review(eb_id, name, actions, rank)
            unmatched += 1

    del PENDING_BATCHES[interaction.channel_id]

    label_suffix = f" — {batch['eb_label']}" if batch["eb_label"] else ""
    summary = (
        f"**EB processed** ({batch['eb_date']}{label_suffix})\n"
        f"Matched to existing accounts: {matched}\n"
        f"Unrecognized names needing review: {unmatched}\n"
    )
    if errors:
        summary += f"\n⚠️ {len(errors)} image(s) failed to process:\n" + "\n".join(errors)
    if unmatched:
        summary += "\nRun `/review_pending` to triage unrecognized names."

    await interaction.followup.send(summary)


# ---------------------------------------------------------------------------
# Reviewing unrecognized names
# ---------------------------------------------------------------------------

@bot.tree.command(name="review_pending", description="See names from screenshots that didn't match any known account.")
async def review_pending(interaction: discord.Interaction):
    pending = db.get_pending_reviews()
    if not pending:
        await interaction.response.send_message("No pending reviews. 🎉", ephemeral=True)
        return

    lines = [f"`#{p['pending_id']}` **{p['extracted_name']}** — {p['successful_actions']} actions (rank {p['rank_in_eb']})"
              for p in pending[:25]]
    msg = (
        "**Pending unrecognized names:**\n" + "\n".join(lines) +
        "\n\nResolve each with one of:\n"
        "`/resolve_as_guest id:<#>`\n"
        "`/resolve_as_alt id:<#> owner_name:<name>`\n"
        "`/resolve_as_correction id:<#> correct_name:<name>`"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="resolve_as_guest", description="Resolve a pending name as a new guest (non-permanent) member.")
async def resolve_as_guest(interaction: discord.Interaction, id: int):
    ok, msg = db.resolve_pending_as_guest(id)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="resolve_as_alt", description="Resolve a pending name as an alt of an existing owner.")
async def resolve_as_alt(interaction: discord.Interaction, id: int, owner_name: str):
    ok, msg = db.resolve_pending_as_alt(id, owner_name)
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="resolve_as_correction", description="Resolve a pending name as a misread of an existing account.")
async def resolve_as_correction(interaction: discord.Interaction, id: int, correct_name: str):
    ok, msg = db.resolve_pending_as_correction(id, correct_name)
    await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@bot.tree.command(name="activity_report", description="Generate an activity report for the last N days.")
@app_commands.describe(days="Number of days to look back")
async def activity_report(interaction: discord.Interaction, days: int = 14):
    await interaction.response.defer()
    report = db.build_activity_report(days)

    def format_section(title, entries, show_owner=False):
        if not entries:
            return f"**{title}**\n_none_\n"
        lines = [f"**{title}**"]
        for e in entries:
            base = f"{e['game_name']} — {e['ebs_attended']}/{e['ebs_total']} EBs, avg {e['avg_actions']} actions"
            if show_owner and e.get("owner_name"):
                base += f"  (owner: {e['owner_name']})"
            lines.append(base)
        return "\n".join(lines) + "\n"

    header = f"📊 **Activity Report — last {days} days** ({report['total_ebs']} EBs run)\n\n"
    body = (
        format_section("PERMANENT MEMBERS", report["permanent"])
        + "\n"
        + format_section("GUESTS", report["guest"])
        + "\n"
        + format_section("ALTS", report["alt"], show_owner=True)
    )

    full_text = header + body
    # Discord message limit is 2000 chars; send as a file if it's long
    if len(full_text) > 1900:
        buf = io.BytesIO(full_text.encode("utf-8"))
        await interaction.followup.send(
            "Report attached (too long for inline message):",
            file=discord.File(buf, filename="activity_report.txt"),
        )
    else:
        await interaction.followup.send(full_text)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
