# Deadguard

A fallback layer that activates when the user has not interacted with the system in a pre-deadline window. Its job is to keep the user from falling behind, not to win the gameweek.

## Why this exists

The user's stated failure mode is mid-season abandonment. Auto mode requires the user to trust the system to make all decisions, which is a high commitment. Manual mode requires the user to be present, which is exactly what fails mid-season.

Deadguard is the middle path: the user stays in Manual mode mentally, but the system has explicit permission to act if and only if the user is absent.

## Per-gameweek state machine

```
PENDING ─────────────────┐
   │                     │
   ├─ user acts ───────→ USER_ACTED
   ├─ system auto acts → SYSTEM_ACTED
   │
   └─ trigger window reached, still PENDING
                         │
                         ↓
                  DEADGUARD_ACTIVE
                         │
              ┌──────────┴──────────┐
              ↓                     ↓
     DEADGUARD_EXECUTED      DEADGUARD_SKIPPED
                              (squad already optimal,
                               no safe action found)
```

Transitions:

- **PENDING → USER_ACTED:** user does anything that counts as an action (see definitions below).
- **PENDING → SYSTEM_ACTED:** in Auto or Hybrid mode, the system has already taken an action for this GW.
- **PENDING → DEADGUARD_ACTIVE:** the trigger window has been reached (default H-30 minutes before deadline) and no user or system action has occurred.
- **DEADGUARD_ACTIVE → DEADGUARD_EXECUTED:** the deadguard took at least one action.
- **DEADGUARD_ACTIVE → DEADGUARD_SKIPPED:** the deadguard ran, but determined no action was needed.

State resets to PENDING at the start of each new gameweek (i.e., after the previous GW finalizes).

## What counts as "user action"

User has acted if, since the last GW transition, the user has done any of:

- Submitted a transfer (manual or via notification confirmation).
- Changed captain or vice manually.
- Modified bench order manually.
- Pressed "I've reviewed, keep as is" in the dashboard or notification.
- Confirmed or declined a system recommendation (both count — declining is an action).
- Activated a chip.

User has **not** acted if they have only:

- Opened the dashboard without doing anything.
- Received a notification without responding.

The "I've reviewed, keep as is" button is non-negotiable. Without it, users have no way to say "I'm happy as is, don't touch my team" without taking some artificial action.

## Trigger windows

Two-stage by default:

- **Warning window (H-120 minutes):** if the GW is still in PENDING state, send a notification: "Deadguard will activate at H-30 if no action is taken."
- **Trigger window (H-30 minutes):** if still PENDING, deadguard runs.

Both windows are user-configurable. Setting `warning_window_minutes` to 0 disables the warning. Setting `trigger_window_minutes` to 0 effectively disables deadguard (use the `enabled: false` flag instead for clarity).

## Scope of deadguard actions

Deadguard is more conservative than full Auto mode because it acts on a user who is, by definition, absent.

### Always allowed (cannot be disabled)

- **Captain & vice selection** based on top xP in squad.
- **Bench order optimization** based on rotation risk and xP.
- **Auto-substitution** for players definitely not playing (flagged out, suspended, removed from squad).

### Allowed if configured (default behavior in `config.yaml`)

- **Single free transfer** if a squad player is flagged out (default `transfer_if_flagged: true`).
- **Single free transfer** if a squad player is clearly underperforming and an obvious upgrade exists (default `transfer_if_underperform: false`).

The threshold for "obvious upgrade" in deadguard mode is stricter than in normal Auto mode: `min_ep_delta_for_transfer` defaults to 3.0 (vs 2.0 in normal Auto). This is deliberate — deadguard should not take borderline calls on behalf of an absent user.

### Forbidden (cannot be enabled)

- **Hits of any size by default.** `allow_hit` defaults to `false`. The user can opt in with a hard cap of -4. Hits beyond -4 are forbidden regardless of config.
- **Chip activation of any kind.** Wildcards, Free Hits, Bench Boosts, Triple Captains. These are strategic decisions and require user presence.
- **Multiple transfers.** Maximum one transfer per deadguard activation.
- **Wildcard-level squad rebuilds.** A deadguard activation cannot rebuild more than a single position.

## Decision flow during deadguard

```
1. Re-fetch latest data (status, lineups, news) — fail safely if data is stale.
2. Captain/vice: run captain ranker, set top-2.
3. Bench: optimize order based on xP and rotation risk.
4. For each squad player:
     If status flag indicates "will not play":
       - Find best free-transfer replacement.
       - If EP delta over 5 GW > threshold AND no hit needed: execute.
       - Else: substitute via bench reorder only.
5. If transfer_if_underperform enabled:
     Find weakest squad player (lowest xP_5gw, with form_adjusted_delta also positive).
     Find best free-transfer upgrade.
     If EP delta > 3.0 AND no hit AND confidence > 75: execute.
6. Log everything. Notify user.
```

## Failure modes and how each is handled

### Data is stale (>12h since last refresh)

Switch to "safe mode": captain, vice, bench order only. Skip all transfers. Log the reason.

### Late team news arrives between deadguard execution and deadline

- If news arrives > 15 minutes before deadline: re-evaluate. If recommendation changes materially, send a notification with one-tap "apply update" or wait 10 minutes for user response. If no response and still > 15 min from deadline, re-execute.
- If news arrives ≤ 15 minutes before deadline: freeze. Do not change anything. Log the missed update.

### User opens the dashboard during deadguard window

The dashboard shows a banner: "Deadguard is about to activate in X minutes. [I've reviewed, keep as is] or make changes to override."

If the user makes a change or presses the button, state transitions to USER_ACTED and deadguard does not run.

### User opens the dashboard after deadguard executed

The dashboard clearly shows what changed, with reasoning and "Undo" buttons where applicable (transfers can be reverted before deadline).

### Transfer submission fails mid-execution

- Retry up to 3 times with exponential backoff.
- If still failing with > 10 minutes to deadline: alert user urgently via all notification channels.
- Captain, vice, bench order are submitted first because they are highest priority and most likely to succeed. Transfer last.

### Multiple devices

Backend is the single source of truth for state. If the user has the dashboard open on phone and laptop, an action on either device updates backend state, which is reflected by the deadguard logic.

### Deadguard runs but finds no action worth taking

State transitions to DEADGUARD_SKIPPED. A notification is still sent: "Deadguard ran, no changes needed. Your team is set."

This is important: a silent deadguard run is indistinguishable from a broken deadguard. Always notify.

## Configuration (recap from `product-spec.md`)

```yaml
deadguard:
  enabled: true
  warning_window_minutes: 120
  trigger_window_minutes: 30
  scope:
    captain_vice: true       # always true, not user-editable
    bench_order: true        # always true, not user-editable
    auto_sub_flagged: true
    transfer_if_flagged: true
    transfer_if_underperform: false
    allow_hit: false
    min_ep_delta_for_transfer: 3.0
    confidence_floor: 75
```

## Logging

Deadguard activations are logged with `mode: "deadguard"` in the activity log, identical schema to other decisions. Additionally, a deadguard-specific summary is generated:

```
{
  "gw": 26,
  "deadguard_activated_at": "2026-02-15T19:30:00Z",
  "reason_for_activation": "No user action since 2026-02-10",
  "last_user_action_at": "2026-02-08T14:22:00Z",
  "actions_taken": [...],     // refs to activity log entries
  "actions_skipped": [...],    // with reasons
  "notification_sent_at": "2026-02-15T19:31:00Z"
}
```

## Interaction with modes

Deadguard interacts with the three modes as follows:

| Mode | Deadguard role |
|---|---|
| Auto | Mostly redundant — Auto would have already acted. Deadguard runs only if Auto skipped (confidence below floor) or failed. |
| Manual | Primary purpose. The user wants control but tolerates a fallback. |
| Hybrid | Secondary. Hybrid auto-handles captain/bench, so deadguard mainly fires for transfers the user ignored. |

The deadguard is disabled if `mode = auto` and `confidence_floor` is set very low — in that case Auto effectively absorbs deadguard's role. But the default config keeps deadguard active in all modes.
