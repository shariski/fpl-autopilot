# Onboarding

First-run setup guide. This doc is written for two audiences:

1. **You, now** — setting up the system for the first time.
2. **You, six months from now** — re-installing on a new machine, or recovering from a wipe.

If a step is unclear, fix the doc as part of resolving it. Future-you will thank present-you.

## What you'll need before starting

- A working FPL account with at least one season's team set up.
- Access to a machine that can run the system (local dev box, home server, or VPS — decision can be deferred per `risks.md` D3).
- A Telegram account.
- A password manager.
- About 30 minutes for the full setup.

## What you'll have at the end

- The system running and serving a dashboard at `http://localhost:8000` (or your chosen host).
- Your FPL squad data populated and refreshed.
- A Telegram bot configured to send you notifications.
- Encrypted credentials stored on disk.
- (After Phase 2) Auto-execution validated through dry-run and ready to go live.

---

## Step 1 — Clone & install

```bash
git clone <repo-url>
cd fpl-autopilot
cp .env.example .env
```

Edit `.env` to fill in any non-secret values (port, healthcheck URL). Secret values (Telegram token, master password) come later.

Install dependencies. Choice of two paths:

**Docker (recommended):**

```bash
docker compose up -d
```

**Native Python:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.scheduler
```

Expected: process is running, no data yet, no credentials yet. Dashboard at `http://localhost:8000` returns a landing page that says "Setup incomplete — run `fpl-autopilot init`."

**Troubleshooting:**

- Port 8000 in use → change `PORT` in `.env`.
- Docker not installed → use the native Python path.
- Python version mismatch → project requires Python 3.11+. Check with `python --version`.

---

## Step 2 — Initialize master password

The master password derives the encryption key for all stored credentials (FPL login, session cookie, anything else sensitive added later).

```bash
fpl-autopilot init-master-password
```

You'll be prompted:

```
Enter master password: ********
Confirm: ********
✓ Salt written to data/.salt
✓ Encryption verified

⚠️  IMPORTANT
This master password cannot be recovered. If you lose it:
  - All stored credentials become unreadable
  - You will need to re-run init-fpl and re-paste your FPL password
  - The encrypted credentials blob is effectively garbage

Store this password in your password manager NOW, before continuing.
```

Implementation notes:

- Argon2id used for key derivation.
- Salt is stored on disk; password is never persisted.
- Every subsequent process start needs the master password, either via the `MASTER_PASSWORD` env var or interactive prompt.
- Minimum 12 characters, mixed character classes enforced.

**Do not skip the password-manager step.** This is the single most common self-hoster regret.

---

## Step 3 — Initialize FPL credentials

```bash
fpl-autopilot init-fpl
```

Prompts:

```
Enter FPL email: you@example.com
Enter FPL password: ********

Attempting login...
✓ Logged in as <FPL display name>
✓ Team ID detected: 1234567
✓ Team name: "<Your Team Name>"

Is this the correct account? [y/N]: y

✓ Credentials encrypted and stored
✓ Session cookie stored (expires ~30 days based on observed behavior)
```

The visual confirmation step ("Is this the correct account?") is deliberate. Typos in email can land you logged into someone else's similarly-spelled account.

**Failure scenarios:**

| Failure | What to do |
|---|---|
| Wrong password | Retry. After 3 failed attempts, command locks for 10 minutes to prevent fat-finger lockout from FPL's side. |
| CAPTCHA required | FPL occasionally serves a CAPTCHA after suspicious activity. Log into FPL via browser once, solve the CAPTCHA, then retry the command. |
| 2FA enabled | FPL does not currently support 2FA. If they add it, this doc needs updating. |
| Network error | Retry. Persistent failure → check FPL website is up at all. |

---

## Step 4 — Initialize Telegram bot

This step has three sub-steps because the Telegram side requires interaction with BotFather.

### 4a. Create the bot

1. Open Telegram. Search for `@BotFather` and start a chat.
2. Send `/newbot`.
3. Choose a display name (anything, e.g. "My FPL Autopilot").
4. Choose a username (must end with `bot`, e.g. `my_fpl_autopilot_bot`).
5. BotFather replies with a token: `123456789:ABCdef-GHI...`. Copy it.

### 4b. Add the token

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdef-GHI...
```

Restart the process so it picks up the new env var.

### 4c. Detect chat ID & verify

Start a chat with the bot you just created (search for its username in Telegram, tap "Start").

Then:

```bash
fpl-autopilot init-telegram
```

Output:

```
Listening for a /start message from your account... (60s timeout)
✓ Detected /start from chat ID 987654321
✓ Chat ID saved to config
✓ Sending test notification with inline button...

(Check Telegram now and tap "Confirm")

✓ Inline button confirmation received
✓ Telegram setup complete
```

The inline button test is end-to-end validation. If the button doesn't reach you, callbacks won't work in production either.

**Failure scenarios:**

| Failure | What to do |
|---|---|
| Token invalid | Re-check the token from BotFather. No spaces, no quotes. |
| No /start received in 60s | You forgot to send /start to your own bot, or you started a different bot. Retry. |
| Notification not received | Check Telegram is online. Check the bot wasn't accidentally blocked. |
| Inline button does nothing | Check process logs — likely a callback URL or polling issue. |

---

## Step 5 — Verify data layer

The data layer needs to be primed before the dashboard makes sense.

```bash
fpl-autopilot refresh --full
```

Expected output:

```
Refreshing bootstrap-static... ✓ (642 players, 20 teams)
Refreshing fixtures... ✓ (380 fixtures)
Refreshing Understat... ✓ (matched 638/642 players)
  ⚠️  4 players unmatched: [list with names]
Computing FDR for next 6 GW... ✓
Computing xP v1 for all players... ✓
Refresh complete in 47s
```

**About the unmatched players:** Understat and FPL use different player IDs. Matching happens by name + team. New transfers, name changes, or unusual spellings can cause mismatches.

For each unmatched player, decide:

- **If irrelevant to your squad and unlikely buy target:** ignore.
- **If in your squad or a buy target:** add a manual mapping in `data/name_resolution.yaml`:
  ```yaml
  # data/name_resolution.yaml maps understat_id -> fpl_id (id-based is robust vs name drift):
  "8260": 12345
  ```
  Then re-run `refresh --full`.

The system will still produce xP for unmatched players using FPL stats only, but accuracy degrades.

---

## Step 6 — First dashboard load

Open `http://localhost:8000`.

You should see:

- Header with current gameweek, deadline countdown, and "Manual" status.
- Your 15-player squad in a pitch view, each with xP for next GW and next 5 GW.
- Captain pick section showing top 5 options with reasoning.
- Transfer ideas (if any are above threshold) showing top 3.
- Chip recommendation (only if conditions are met).
- Fixture planner: a 5×6 grid of your squad against next 5 GW, color-coded by custom FDR.
- Activity log: empty for now.

### Sanity-check checklist

Walk through this list. If any item fails, debug before continuing.

- [ ] Squad shows exactly 15 players.
- [ ] Every player has an xP value (not NaN, not blank).
- [ ] Captain pick shows familiar premium players near the top (Haaland / Salah / Saka caliber, not a backup goalkeeper).
- [ ] Each transfer suggestion is a valid swap: same position, price within budget, no 3-per-club violation.
- [ ] Fixture planner colors: away matches against strong defenses look harder (darker) than home matches against weak defenses.
- [ ] Deadline countdown matches FPL's official deadline.

### Common issues

| Symptom | Likely cause |
|---|---|
| xP is NaN for some players | Understat data missing for that player. Check unmatched list from Step 5. |
| Captain pick suggests a low-priced midfielder | xMinutes weighting probably broken — a player with 0 recent minutes should not be at the top. Check rolling-minutes computation. |
| All players in squad have identical fixture difficulty | FDR is not differentiating attack vs defense, or home/away factor is missing. |
| Dashboard shows "no squad data" | Step 3 didn't persist correctly. Re-run `fpl-autopilot init-fpl`. |

---

## Step 7 — (Phase 2) Choose your mode

Available after Phase 2 ships.

```bash
fpl-autopilot config mode manual    # default — every action requires confirmation
fpl-autopilot config mode hybrid    # captain/bench auto, transfers/chips notify
fpl-autopilot config mode auto      # everything auto except chips
```

Recommendation for first time: **manual**. Get comfortable with the Telegram flow before granting execution authority.

---

## Step 8 — (Phase 2) Configure deadguard

```bash
fpl-autopilot config deadguard on
fpl-autopilot config deadguard --trigger-window 30 --warning-window 120
```

The defaults match `product-spec.md`. See `docs/deadguard.md` for full scope and configuration options.

To opt into deadguard executing transfers when you've been silent:

```bash
fpl-autopilot config deadguard --transfer-if-flagged true
fpl-autopilot config deadguard --transfer-if-underperform false   # safer default
```

Hits in deadguard are off by default and capped at -4 even if enabled. Chips are always off.

---

## Step 9 — (Phase 2) Dry-run validation

**This step is non-optional before going live with Auto mode.**

```bash
fpl-autopilot config dry-run on
```

Run for **at least 3 consecutive gameweeks**. During each:

- Continue making your usual manual decisions.
- The system will log what it *would have done* in dry-run mode.
- After each gameweek settles, compare via:

```bash
fpl-autopilot dry-run report --gw <N>
```

Output format:

```
GW 12 Dry-Run Report
====================
Captain:
  System: Haaland (xP 7.2, confidence 82)
  You:    Haaland
  Match: ✓

Transfer:
  System: Saka → Palmer (free, +3.1 EP over 5 GW, confidence 78)
  You:    no transfer
  Match: ✗
  Outcome (after GW): Saka 4pts, Palmer 9pts. System +5 vs your choice.

Bench order:
  System: GK2, Mbeumo, Anderson, Branthwaite
  You:    GK2, Mbeumo, Branthwaite, Anderson
  Match: ✓ (no auto-sub triggered, ordering didn't matter)
```

### Criteria to graduate from dry-run to live Auto

- ≤ 1 decision per gameweek where system and your manual choice significantly diverge.
- For divergent decisions: in retrospect, you'd rather have gone with the system's choice ≥ half the time. If the system is mostly wrong, do not turn on Auto. Investigate why first.
- No data-layer errors or schema warnings during the 3 GW window.

If you fail criteria: stay in dry-run, file what went wrong as a risk in `docs/risks.md`, and re-evaluate when fixed.

---

## Step 10 — (Phase 2) Go live

```bash
fpl-autopilot config dry-run off
fpl-autopilot config mode auto    # or keep manual / hybrid if preferred
```

The first auto-execution after going live triggers an extra Telegram confirmation:

```
⚠️ FIRST LIVE AUTO-EXECUTION

Action: Captain set to Haaland for GW 13
Confidence: 82
This is your first live auto-execution. Confirm to proceed.

[✅ Yes, proceed] [❌ No, stay in manual]
```

This confirmation happens exactly once per mode change to auto. After that, auto mode runs without per-action confirmation (per `decision-engine.md`).

---

## Persistent setup status (dashboard banner)

The dashboard shows a persistent banner if setup is incomplete. Examples:

- "⚠️ Telegram not configured — you will not receive notifications." → run `init-telegram`.
- "⚠️ Understat data is more than 7 days stale." → run `refresh --full`, investigate scraper.
- "⚠️ FPL session expired and re-login failed twice." → run `init-fpl`.
- "⚠️ Dry-run is on — no actions will be executed." → reminder, not error.

These banners are intentionally annoying. Half-completed setups are worse than no setup at all.

---

## Recovery / re-installation

If you're re-installing on a new machine, or recovering from a disk wipe, you have two paths.

### Path A — You have backups (recommended)

What you should have backed up:

- `data/fpl_autopilot.db` (SQLite, contains all history and encrypted credentials)
- `data/.salt` (without this, the master password can't decrypt anything)
- `.env` (Telegram token, config flags)
- `config.yaml` (your mode settings, thresholds, deadguard config)

Procedure:

1. Clone repo on new machine.
2. Restore the four files above into their original paths.
3. Run the process. You'll be prompted for the master password.
4. Verify dashboard loads and shows current squad.

No re-init of FPL or Telegram is needed.

### Path B — No backups

1. Clone repo on new machine.
2. Run `init-master-password` (new password, since you don't have the old salt).
3. Run `init-fpl`, `init-telegram`, `refresh --full`.
4. You lose: activity log history, dry-run reports.
5. You keep: nothing automated. Fresh start.

**Mitigation:** automate backups. A weekly cron that copies the four files to cloud storage (rclone to your storage of choice) is sufficient.

---

## Reference: useful commands after onboarding

```bash
fpl-autopilot status                       # current mode, last action, next scheduled job
fpl-autopilot refresh                      # incremental refresh (faster than --full)
fpl-autopilot refresh --full               # full re-fetch from all sources
fpl-autopilot freeze                       # emergency stop all automation
fpl-autopilot unfreeze                     # resume automation
fpl-autopilot log --tail 20                # last 20 activity log entries
fpl-autopilot log --gw 12                  # all decisions for GW 12
fpl-autopilot config list                  # show current config
fpl-autopilot config <key> <value>         # set a config value
```
