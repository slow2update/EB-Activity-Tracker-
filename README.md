# Kingdoms at War — Activity Tracker Bot

Automates EB (Epic Battle) activity checks: upload roster screenshots,
the bot reads them, tallies participation per member, and produces
alphabetized reports broken into Permanent Members / Guests / Alts.

---

## 1. One-time setup

### A. Create the Discord bot
1. Go to https://discord.com/developers/applications → **New Application**.
2. Name it (e.g. "EB Activity Tracker") → **Create**.
3. Left sidebar → **Bot** → **Add Bot**.
4. Under **Privileged Gateway Intents**, turn ON **Message Content Intent**
   (the bot needs this to see screenshot attachments).
5. Click **Reset Token** → copy the token. This is your `DISCORD_TOKEN`.
   Keep it secret — anyone with it can control your bot.
6. Left sidebar → **OAuth2 → URL Generator**:
   - Scopes: check `bot` and `applications.commands`
   - Bot Permissions: check `Send Messages`, `Read Message History`,
     `Attach Files`, `Add Reactions`, `Use Slash Commands`
   - Copy the generated URL, open it in your browser, and add the bot to
     your Discord server.

### B. Get an Anthropic API key
1. Go to https://console.anthropic.com → **API Keys** → **Create Key**.
2. Add a small amount of credit (a few dollars covers a long time at this
   usage volume — a handful of screenshots per EB, a few EBs a week).
3. Copy the key. This is your `ANTHROPIC_API_KEY`.

### C. Deploy to Railway (recommended — no server management)
1. Push this folder to a new GitHub repo (private is fine).
2. Go to https://railway.app → sign in with GitHub → **New Project** →
   **Deploy from GitHub repo** → select your repo.
3. In the Railway project → **Variables** tab, add:
   - `DISCORD_TOKEN` = (from step A)
   - `ANTHROPIC_API_KEY` = (from step B)
4. Railway will detect `requirements.txt` and run `python bot.py`
   automatically. If it doesn't, set the start command manually to
   `python bot.py` under **Settings → Deploy**.
5. Watch the **Deployments → Logs** tab for `Logged in as ... Slash
   commands synced.` — that confirms it's live.

The SQLite database file (`data/activity.db`) lives inside the Railway
container's filesystem. Railway's free/starter tiers persist a volume
across restarts, but if you ever redeploy from scratch, back up that file
first (see Maintenance section below).

---

## 2. Running it locally first (recommended before deploying)

This lets you test on your own machine before trusting it on Railway.

```bash
cd kingdoms-bot
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste in your real tokens

# Load the .env file into your shell, then run:
export $(cat .env | xargs)   # macOS/Linux
python bot.py
```

(On Windows, use `set` commands or a tool like `python-dotenv` — ask if
you want this added directly into bot.py so you don't need to export
manually.)

---

## 3. Day-to-day usage

### Recording a new EB
1. In your admin channel, run:
   `/start_eb_upload eb_date:2026-06-27 eb_label:Neidria of the Hoarfrost`
2. Post all your scrolling screenshots for that EB as image attachments,
   in any order, in that same channel. The bot reacts ✅ to each one it
   picks up.
3. Run `/process_eb`. The bot reads every screenshot, matches names
   against known accounts, and tells you how many matched vs. need review.

### Triaging unrecognized names
Run `/review_pending` to see any names the bot couldn't match. For each:
- `/resolve_as_guest id:<#>` — it's a new non-member hopper
- `/resolve_as_alt id:<#> owner_name:<existing_name>` — it's someone's alt
- `/resolve_as_correction id:<#> correct_name:<existing_name>` — it was a
  misread of an existing name (e.g. OCR confused `-ALCH3MY-` for
  `-ALCH3MY1-`)

### Self-service (any member can run these)
- `/register game_name:MyIGN` — link your Discord account to your main
- `/addalt alt_game_name:MyAltIGN` — add your own alt
- `/addmember game_name:NewPersonIGN` — add someone new to the permanent roster

### Admin alt management
- `/alt link alt_name:X owner_name:Y`
- `/alt unlink alt_name:X`
- `/alt list owner_name:Y`

### Reports
`/activity_report days:14` — posts (or attaches, if long) three
alphabetized lists: Permanent Members, Guests, and Alts, each showing
EBs attended out of total EBs run in that window, and average successful
actions per EB.

---

## 4. How name-matching safety works

The bot **never fuzzy-matches** names — `-ALCH3MY-`, `-ALCH3MY-D3M0N-`, and
`-ALCH3M1C4L-` are kept as three completely distinct accounts, because
that's exactly the kind of near-identical naming this game's playerbase
uses for legitimate alts and unrelated players alike. Any name not seen
before goes to `/review_pending` for a human decision rather than being
silently guessed at.

---

## 5. Maintenance

**Backing up your data:** the entire bot's memory is the single file
`data/activity.db`. Download it periodically (Railway → your service →
the file browser, or add a `/backup` command if you want the bot to DM
it to you — ask and I'll add this).

**Extending later:** if you outgrow Railway's free tier or want a
dedicated server, the bot code is unchanged — just point it at a new
host with the same two environment variables and copy `data/activity.db`
over.
