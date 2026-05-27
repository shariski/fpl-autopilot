# Runbook

Crisis response procedures. This doc is written for the moment when something is broken and a deadline is approaching. It is not for steady-state work.

## How to use this doc

1. Find the section that matches your symptom.
2. Follow the steps in order. Don't skip.
3. If you successfully recover, write what fixed it under "Notes" in that section. Future-you needs the data.
4. If nothing in this doc matches your symptom, jump to **§13 Unknown failure** at the bottom.

Severity legend used below:

- 🔴 **Critical** — deadline is in ≤ 2 hours. Triage and act fast.
- 🟡 **Urgent** — deadline within 24 hours. Fix today.
- 🟢 **Standard** — no immediate deadline pressure. Fix correctly, not fast.

---

## §1 — Scheduler missed a scheduled job 🔴

**Symptom:** healthcheck endpoint didn't ping at the expected time. UptimeRobot / Healthchecks.io alert fired.

### Immediate triage

1. Check process is running:
   ```bash
   docker ps   # or: ps aux | grep fpl-autopilot
   ```
2. If process is down: skip to **§2 Process crashed**.
3. If process is up: check logs for the missed job time:
   ```bash
   fpl-autopilot log --tail 100 | grep -i schedule
   ```

### If deadline is within 2 hours

Manually run what should have run:

```bash
fpl-autopilot refresh                          # if pre-deadline refresh was missed
fpl-autopilot deadguard --force-check          # forces deadguard state evaluation
```

For captain / bench / transfer that should have already been submitted: open the dashboard and execute manually via Telegram inline buttons, or via direct UI.

**Do not** trust the scheduler to "catch up." It might not.

### Root-cause after the deadline passes

- Check APScheduler job store: `sqlite3 data/fpl_autopilot.db "select * from apscheduler_jobs;"`
- Look for stuck jobs (next_run_time in the past).
- Common cause: process restart without persistent job store, or DB lock during refresh.

### Notes

_(Add what fixed it the first time this happens, so future-you doesn't re-debug.)_

---

## §2 — Process crashed 🔴

**Symptom:** dashboard returns connection refused; Telegram bot doesn't respond.

### Immediate triage

1. Check exit status:
   ```bash
   docker compose logs --tail 200 fpl-autopilot
   # or: tail -200 data/logs/app.log
   ```
2. Look at the last error or stack trace.

### Restart

```bash
docker compose restart fpl-autopilot
# or for native:
python -m src.scheduler &
```

Wait 30 seconds. Then verify:

```bash
fpl-autopilot status
```

Expected: shows current mode, last action, next scheduled job.

### If restart loops

Process starts, then crashes again within seconds. Likely a corrupt config, missing env var, or recently-broken dependency.

1. Check env vars are present:
   ```bash
   docker compose exec fpl-autopilot env | grep -E "(MASTER_PASSWORD|TELEGRAM_BOT_TOKEN)"
   ```
2. If MASTER_PASSWORD is missing or wrong: process can't decrypt anything. Restart with the correct value.
3. If TELEGRAM_BOT_TOKEN is missing: process will refuse to start by design.

### If deadline is within 2 hours and restart fails

Open the FPL website directly. Make changes manually. The system can recover later; the deadline cannot.

### Notes

---

## §3 — FPL session expired and re-login fails 🟡

**Symptom:** Dashboard banner says "FPL session expired and re-login failed twice." Activity log shows 401 / 403 responses.

### Triage

```bash
fpl-autopilot init-fpl --reauth
```

Prompts for password (email is remembered). Tries to re-establish session.

### If re-login still fails

| Cause | Response |
|---|---|
| Wrong password (recently changed) | Re-run with new password. |
| CAPTCHA challenge | Open FPL in browser, log in, solve CAPTCHA, retry. |
| Account temporarily blocked | Wait 30 minutes, retry. If it persists, see **§4 Account flagged**. |
| FPL site is down | Verify at status.premierleague.com or fantasy.premierleague.com. Wait it out. |

### While the session is broken

The system continues to read public data (squad picks, prices) but cannot submit any action. The dashboard banner stays up. No auto-execution happens. Deadguard cannot fire.

If a deadline is approaching: act manually via the FPL website. Re-establish session afterward.

### Notes

---

## Emergency override (freeze / kill-switch)

A freeze halts ALL autonomous FPL writes — auto-mode `auto_execute_job` and the entire
`run_deadguard_job`. A user's explicit Telegram **Confirm** is still honoured (freeze stops
autonomy, not deliberate action).

- **Freeze:** `fpl-autopilot freeze [--reason "..."]`, or tap 🛑 Freeze on the deadguard warning /
  an auto-mode execution notice (Telegram, requires `telegram.interactive`).
- **Unfreeze:** `fpl-autopilot unfreeze`, or tap ▶️ Unfreeze on the freeze confirmation message.
- **Status:** `fpl-autopilot freeze-status`, or the `frozen:` / `relogin_failures:` lines in
  `fpl-autopilot auth-status`.
- **No master password is required** to freeze/unfreeze — the freeze is plaintext operational state.
- **Dashboard controls (Phase 2.5c-3):** the **Freeze / Unfreeze** toggle in the dashboard header
  and the **Keep as is** banner button also write freeze/deadguard state via `POST /api/freeze`,
  `POST /api/unfreeze`, `POST /api/deadguard/keep` — same effect as the CLI commands above.

### `fpl-autopilot serve` binds localhost by default (Phase 2.5c-3)

`fpl-autopilot serve` binds to `127.0.0.1` (localhost) by default. This is intentional: the API now
exposes state-mutating endpoints (`/api/freeze`, `/api/unfreeze`, `/api/deadguard/keep`) that carry no
authentication — exposing them on an open interface would be unsafe. To deliberately expose the dashboard
on the local network (e.g. to reach it from a phone on the same Wi-Fi), pass `--host 0.0.0.0` and accept
that anyone on the LAN can trigger a freeze or suppress deadguard.

### Automatic freeze (B7)
After **two consecutive** failed token refreshes, the system freezes itself (`source="auto"`) and
alerts once. Unfreezing alone will not help — the refresh token is still bad. Recover by:
1. `fpl-autopilot init-fpl` (paste a fresh refresh token) — this resets `relogin_failures` to 0.
2. `fpl-autopilot unfreeze`.

---

## §4 — Account flagged or banned by FPL 🔴

**Symptom:** Login fails with a message about suspicious activity, or the FPL site shows the account is restricted.

**Reality check:** this is R3 materializing. The risk you accepted.

### Triage

1. Stop all automation immediately:
   ```bash
   fpl-autopilot freeze
   ```
2. Do NOT retry login repeatedly. That makes things worse.
3. Log into FPL via browser. Read any messages from FPL.

### If it's a soft block (CAPTCHA, "verify you're human")

- Solve the challenge in browser.
- Wait 1 hour.
- Try one re-login via the system.

### If it's a hard block (account suspended)

- Do not attempt to circumvent.
- Contact FPL support if you believe it's a mistake.
- Treat the season as manual-only until resolved.

### Prevention going forward

- Reduce request rate further (the default is already ≤ 1 req/sec; consider going lower).
- Verify User-Agent is realistic.
- Avoid bursts of activity.
- If a second flag happens: stop using auto-execution permanently. Phase 2 was a risk and it didn't pay off for you.

### Notes

---

## §5 — FPL API schema changed 🟡

**Symptom:** Schema-assertion tests fail. Logs show "schema drift detected" with a field name. Some computations may have started producing wrong values silently before the assertion caught it.

### Triage

1. Look at the assertion error to identify the changed field.
2. Compare to the FPL API response directly:
   ```bash
   curl -s https://fantasy.premierleague.com/api/bootstrap-static/ | jq '.elements[0]'
   ```
3. Identify whether the change is:
   - Field renamed (`now_cost` → `current_cost`)
   - Field type changed (integer → string)
   - Field removed entirely
   - New field added (these should never fail assertions, only log warnings)

### Fix

1. Update the schema assertion in `src/data/fpl_client.py` (or wherever it lives).
2. Update any consumer that touched the renamed/removed field.
3. Re-run tests.
4. Manually trigger `refresh --full` and verify data populates correctly.

### If deadline is within 24 hours and you can't fix in time

```bash
fpl-autopilot freeze              # stop auto-execution
```

Make decisions manually for this gameweek. Fix the schema issue after deadline.

### Notes

---

## §6 — Understat / FBref scraping broken 🟢

**Symptom:** xP confidence dropped for many players. Activity log shows scraper errors. Dashboard banner says "Understat data is stale."

### Triage

```bash
fpl-autopilot refresh --source understat --verbose
```

Look at the error. Common causes:

| Cause | Response |
|---|---|
| HTML structure changed | Scraper needs update. Inspect the page manually, adjust selectors. |
| Rate limited | Wait 6 hours. Try again. |
| Cloudflare challenge | Switch to FBref as backup source temporarily. |
| Site completely down | Wait it out. Use FPL stats only meanwhile. |

### Operating mode while scraping is broken

The system falls back to FPL native stats. xP becomes less accurate but is not nonsense. Confidence scores automatically drop, which means Auto mode will defer to manual notification more often.

This degrades gracefully on purpose. You don't have to do anything urgent.

### Notes

---

## §7 — Transfer submission stuck or failed 🔴

**Symptom:** Activity log shows transfer attempted but never confirmed. Telegram notification didn't arrive. Dashboard shows uncertain state.

### Triage

1. **First, check FPL directly.** Open the FPL website, look at your squad. The transfer may have actually gone through despite the system thinking it failed.
2. If transfer went through on FPL side: the system has a logging inconsistency, not a functional problem. Mark this in the activity log manually and move on. Fix the inconsistency after deadline.
3. If transfer did NOT go through on FPL side and deadline is within 1 hour: **do it manually now**. Don't troubleshoot the system first.

### After the urgent moment

```bash
fpl-autopilot log --gw <N> --decision-type transfer
```

Look at the inputs the system used. Verify they made sense. Then:

```bash
fpl-autopilot refresh --full
```

To ensure state is consistent. The next scheduled action will resume normal behavior.

### If the failure mode repeats

The Action Executor has a retry loop. If it's failing 3+ times consistently, something is wrong at the API level. Likely culprits:

- Session expired between login check and submission (race condition).
- 3-per-club rule violated (shouldn't happen, but if the transfer engine has a bug, it could).
- Insufficient bank (shouldn't happen, same).

Inspect the executor logs for the specific HTTP response from FPL.

### Notes

---

## §8 — Deadguard fired wrong 🟡

**Symptom:** Deadguard activated and did something you wouldn't have done. The action already went through.

### Reality check first

Was it actually wrong? Open the activity log:

```bash
fpl-autopilot log --gw <N> --mode deadguard
```

Read the inputs and alternatives. Sometimes deadguard makes a defensible call that *feels* wrong but isn't.

If after reading, the action was genuinely a bad call:

### Damage control

1. Can the transfer be undone?
   ```bash
   fpl-autopilot undo --gw <N> --action transfer
   ```
   Only works if before deadline.
2. Captain / vice / bench: change manually via dashboard. Cheap to fix.
3. Chip activation: **deadguard should never activate a chip**. If it did, this is a code bug, file it as a P0.

### Root cause

Common causes for "deadguard did the wrong thing":

- Stale data: it acted on injury info that was outdated.
- Threshold misconfigured: `min_ep_delta_for_transfer` too low.
- xP model edge case: a player with broken xMinutes scored unexpectedly high.

Tighten the relevant config:

```bash
fpl-autopilot config deadguard --transfer-if-underperform false
fpl-autopilot config deadguard --min-ep-delta 4.0
```

### Notes

---

## §9 — Telegram bot stopped responding 🟡

**Symptom:** Sending /status to the bot returns nothing. Inline buttons don't trigger callbacks.

### Triage

1. Check process is running (see **§2**).
2. Check Telegram token is still valid:
   ```bash
   curl https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe
   ```
   Expected: JSON with `"ok": true` and bot info.
3. If token returns 401: token was revoked. Re-create via BotFather, update `.env`, restart.
4. If token is valid but bot doesn't respond: polling thread might be stuck. Restart process.

### While the bot is broken

- Dashboard still works. Use the web UI.
- Auto-execution still happens (it doesn't depend on Telegram).
- You just lose one-tap confirmation. Manual mode becomes "open dashboard and act."

Not a critical failure unless combined with another issue.

### Known quirks

**Double-FROZEN (or double-confirm) notifications.** Telegram does not auto-disable
inline buttons after a tap. If you tap the same button twice before the poller
processes the first callback, both callbacks land in the same `getUpdates` batch
and the handler runs twice. The DB write paths are idempotent (`override.freeze`,
`set_pending_status`), so state is correct, but you'll receive two confirmation
notifications. Harmless. Hardening (deferred): after the first handle, call
`editMessageReplyMarkup` on the original message to strip the buttons so the
second tap returns "Already handled" via `answer_callback_query` instead of
firing again. See `src/interface/telegram_interactive.py:174` (`handle_freeze`)
and `:185` (`handle_unfreeze`).

### Notes

---

## §10 — Dashboard shows wrong squad or wrong gameweek 🟡

**Symptom:** Squad on dashboard doesn't match FPL website. Or gameweek counter is off by one.

### Triage

Usually a data refresh issue.

```bash
fpl-autopilot refresh --full
```

Then hard-refresh the dashboard (Ctrl+Shift+R / Cmd+Shift+R).

### If still wrong

Check the database directly:

```bash
sqlite3 data/fpl_autopilot.db
> select * from gameweeks where is_current = 1;
> select * from my_team order by gw desc limit 1;
```

If the DB has wrong data: the refresh logic has a bug or the FPL API returned something weird. Re-run refresh; if still wrong, delete the most recent `my_team` snapshot and re-refresh:

```sql
DELETE FROM my_team WHERE gw = <wrong_gw>;
```

Then refresh again.

### Notes

---

## §11 — Master password forgotten 🟢

**Symptom:** You don't remember the master password and can't restart the process.

### Reality

By design, this is **unrecoverable**. The encryption key derives from the master password. No master password → no key → encrypted credentials are noise.

### What you can recover

- Activity log history (it's in plaintext in the DB).
- Configuration (also plaintext).
- Squad snapshots (plaintext).

### What you can't recover

- Stored FPL credentials (re-enter via `init-fpl`).
- Stored Telegram bot token (still in `.env`, not encrypted there, so this survives).
- Stored session cookie (re-issued on next login).

### Procedure

```bash
# Backup current state
cp data/fpl_autopilot.db data/fpl_autopilot.db.bak.$(date +%Y%m%d)

# Wipe encrypted credentials
sqlite3 data/fpl_autopilot.db "DELETE FROM credentials;"
rm data/.salt

# Re-init
fpl-autopilot init-master-password    # new password
fpl-autopilot init-fpl                # re-enter FPL credentials
```

All history and config remains intact. You just lost the encryption shell.

### Prevention

Save the master password in a password manager. This warning was already given in `onboarding.md` Step 2. There is no second chance.

### Notes

---

## §12 — Database corrupted 🟢

**Symptom:** SQLite errors on read or write. "database disk image is malformed" or similar.

### Triage

```bash
sqlite3 data/fpl_autopilot.db "PRAGMA integrity_check;"
```

If clean: not actually corrupted, you have a different issue.

If corrupted: try to recover what you can.

```bash
sqlite3 data/fpl_autopilot.db ".dump" > dump.sql
# Inspect dump.sql for what survived

sqlite3 data/fpl_autopilot.db.recovered < dump.sql
```

### Restore from backup

If you have a recent backup (see `onboarding.md` recovery section):

```bash
cp data/backup/fpl_autopilot.db.YYYYMMDD data/fpl_autopilot.db
```

Restart the process. Verify dashboard loads.

### If no backup

Treat as a fresh install. Re-onboard. Activity log is gone. xP model history is gone. Squad snapshot history is gone. You keep: the codebase, your master password, your FPL account itself.

### Prevention

Automate backups. The four files to back up are listed in `onboarding.md` recovery section. A weekly cron is sufficient. A daily cron is better.

### Notes

---

## §13 — Unknown failure 🔴 / 🟡 / 🟢

If none of the above sections match.

### Step 1 — Don't make it worse

```bash
fpl-autopilot freeze
```

This stops auto-execution. Manual actions via dashboard still work.

### Step 2 — Collect information

```bash
fpl-autopilot status > /tmp/status.txt
fpl-autopilot log --tail 200 > /tmp/log.txt
docker compose logs --tail 500 > /tmp/docker.txt
# or: tail -500 data/logs/app.log > /tmp/app.txt
```

### Step 3 — Triage by deadline

If deadline within 2 hours → make decisions manually via FPL website. Debug later.

If deadline > 2 hours → take time to read logs, isolate the failure.

### Step 4 — Add a section to this runbook

Once you figure out what was broken: write a new section. Copy the format. Future-you will benefit.

---

## Appendix — Useful diagnostic commands

```bash
fpl-autopilot status                       # current mode, last action, next job
fpl-autopilot log --tail N                 # last N activity entries
fpl-autopilot log --gw N                   # all decisions for GW N
fpl-autopilot log --mode deadguard         # filter by mode
fpl-autopilot log --decision-type transfer # filter by type
fpl-autopilot freeze                       # emergency stop
fpl-autopilot unfreeze                     # resume
fpl-autopilot refresh-my-team              # one-shot authed /api/my-team snapshot; prompts for master password

# Inspecting DB directly
sqlite3 data/fpl_autopilot.db
> .tables
> .schema <table>
> PRAGMA integrity_check;

# Inspecting FPL API directly
curl -s https://fantasy.premierleague.com/api/bootstrap-static/ | jq '.elements[0]'
curl -s https://fantasy.premierleague.com/api/entry/<team_id>/ | jq

# Checking Telegram bot
curl https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe
```

---

## Appendix — Backup automation

A weekly cron to a remote location. Adapt to your storage of choice.

```bash
# crontab -e
0 3 * * 0  cd /path/to/fpl-autopilot && tar czf /tmp/fpl-backup-$(date +\%Y\%m\%d).tar.gz data/fpl_autopilot.db data/.salt .env config.yaml && rclone copy /tmp/fpl-backup-*.tar.gz <remote>:/fpl-backups/ && rm /tmp/fpl-backup-*.tar.gz
```

Verify backups quarterly by doing a dry-run restore on a different directory.

What's backed up:
- `data/fpl_autopilot.db` — all history, encrypted credentials
- `data/.salt` — required to decrypt credentials
- `.env` — Telegram token, healthcheck URL
- `config.yaml` — mode settings, thresholds

Master password is **not** backed up. You backed that up in a password manager during onboarding.
