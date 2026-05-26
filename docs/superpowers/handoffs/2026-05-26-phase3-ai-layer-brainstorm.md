# Handoff prompt — Phase 3 (AI Layer) brainstorming

**For:** a parallel Claude Code session working out of a separate worktree.
**Created:** 2026-05-26
**Task type:** brainstorming + design only. NO implementation.

---

## Copy-paste prompt for the new session

> You are starting a fresh Claude Code session on the **FPL Autopilot** project (a
> personal Fantasy Premier League assistant). A parallel agent is currently
> working on Phase 2 leftover wiring in the main checkout. You are working in
> an isolated git worktree at the path the user gave you.
>
> **Your single task this session is brainstorming the design of Phase 3 (the
> AI Layer). DO NOT write any implementation code. DO NOT scaffold files.
> DO NOT pick a library and install it. Brainstorm only. Produce a spec.**
>
> ### Read these first, in order
>
> 1. `CLAUDE.md` — project rules (Parts A and B). Sections B3, B4, B7, B8, B11,
>    B12, B13 are the binding ones for you.
> 2. `docs/superpowers/HANDOFF.md` — full state of Phases 1 and 2. Pay attention
>    to the "Auth reality" section and the "Findings / backlog (for Phase 3)"
>    block at the bottom.
> 3. `docs/plan.md` — see the Phase 3 section. **It is intentionally
>    under-detailed by design.** Your job is to detail it now.
> 4. `docs/decision-engine.md` — the changelog runs up to v0.11. **B4 is
>    "sacred":** any AI feature that changes how recommendations are *produced*
>    (vs. how they are *explained*) needs this document updated FIRST.
> 5. `docs/product-spec.md` — for the original Phase 3 intent.
> 6. `docs/architecture.md` — the four-layer model. The AI Layer slots in.
> 7. `docs/risks.md` — for R3 (the agent never executes live FPL writes) and
>    other rolling risks.
> 8. Skim `docs/superpowers/specs/` — the most recent specs show the project's
>    house style.
>
> ### The brainstorming question
>
> Phase 3's stated goal (from `plan.md`):
>
> > Decisions are accompanied by natural-language reasoning, the system
> > understands the user's mini-league context, and personalization is based on
> > observed user behavior.
>
> Seven placeholder bullets are listed in `plan.md`. You will NOT try to design
> all seven. Instead:
>
> **Phase 1 of your brainstorm — scope & decomposition.** Which one slice
> ships first? Argue from value, risk, dependency order, and the current
> state of `activity_log` (it has only ~1 GW of real data so far). Decompose
> the seven placeholders into independent, orderable sub-slices and recommend
> the first one. The user picks.
>
> **Phase 2 of your brainstorm — design the AI architecture itself.** Before
> any single slice can be built, the project needs to settle:
>
> - **Provider & model.** Claude API (Anthropic), local (Ollama / llama.cpp),
>   or both with a routing rule? Cost ceiling? Latency budget?
> - **Where does the model run in the architecture?** Per the four-layer model
>   in `architecture.md`, the AI Layer needs to plug in somewhere. Does it sit
>   between Decision and Interface (post-process recommendations into prose)?
>   Or alongside Decision (as a separate, parallel reasoning track)? Or
>   purely in Interface (chat-only, no recommendation involvement)?
> - **Prompt + context strategy.** What does the model see? The whole
>   `activity_log`? Just the latest GW? A pre-summarised digest? Mini-league
>   standings (when wired)? Player news? How is data fed in — JSON, prose,
>   structured tool calls?
> - **B4 boundary.** Does the AI ever *change* a recommendation, or only
>   *describe* one? If it describes, it's a pure addition and B4 is untouched.
>   If it can override, `decision-engine.md` needs a new version. The default
>   should be **describe-only** for the first slice — surface this explicitly.
> - **Caching.** LLM calls are slow and cost money. Cache per-GW per-player?
>   Per-recommendation? Invalidate on what? Where stored?
> - **Failure modes.** Provider down, rate limit, malformed response. Does the
>   dashboard fall back to template strings? Does it block? What does the
>   user see?
> - **R3.** The agent never executes live FPL writes. The AI Layer should
>   never gain that power either — its outputs are text or structured
>   suggestions, never `requests.post` to FPL.
> - **B7.** No credentials in prompts. No raw cookies in the LLM context
>   window. Whatever digest you build must be sanitised.
> - **Activity-log dependency.** Phase 3 in `plan.md` says "Detail this phase
>   after Phase 2 is operational and the system has accumulated real
>   activity-log data." The repo has ~1 GW. What can ship now (template-free
>   reasoning, no personalisation), and what genuinely needs to wait (the
>   user-history analyzer, the "why did you pick X" conversational query)?
>
> ### Conventions you MUST follow
>
> - **Superpowers flow:** brainstorming → spec (in `docs/superpowers/specs/`)
>   → writing-plans (in `docs/superpowers/plans/`) → subagent-driven-development
>   (fresh subagent per task, TDD, commit each). You are in the brainstorming
>   phase. Stop at the spec.
> - **Spec naming:** `docs/superpowers/specs/2026-05-XX-phase3-<slice>-design.md`.
> - **Branch:** `feat/phase3-<slice>` in your worktree.
> - **NEVER `git add -A`.** Stage explicit paths. The repo contains
>   `.claude/worktrees/` gitlinks that get swept up otherwise.
> - **Commits:** Co-Authored-By footer with the Claude line (see other commits).
> - **Tests are fixtures-only (R3).** No live calls, even in your test fixtures.
> - **Doc-first for B4.** If your design includes anything that changes
>   *what* a recommendation is, you write the `decision-engine.md` entry as
>   part of the spec. The default first-slice should NOT trip this.
>
> ### What you produce this session
>
> 1. A **scope-and-decomposition memo** at the top of the spec, listing the
>    seven Phase 3 bullets, decomposing them into independent slices,
>    explaining dependencies, and recommending one slice to go first with
>    rationale.
> 2. A **cross-cutting AI architecture spec** for the AI Layer (provider,
>    model, where it plugs in, prompt strategy, caching, failure modes,
>    B4/B7/R3 stance). This is reused by every Phase 3 slice that comes
>    after — write it once, get it right.
> 3. A **first-slice design spec** for the slice you recommend, in the
>    house style of `docs/superpowers/specs/2026-05-24-dashboard-deadguard-controls-design.md`.
>
> All three can live in one spec file or three — your call, but link them.
>
> ### What you do NOT do this session
>
> - Do not write any code in `src/`.
> - Do not install any package.
> - Do not call `init-master-password`, `init-fpl`, or any `--live` CLI.
> - Do not write a `writing-plans` plan. That's the next session, after the
>   user reviews this spec.
> - Do not touch `docs/decision-engine.md` unless your design genuinely
>   changes decision logic (it shouldn't for the first slice).
>
> ### Worktree setup the user is going to run before invoking you
>
> ```bash
> cd /Users/shariski/Work/fpl-autopilot
> git fetch origin
> git worktree add ../fpl-autopilot-phase3 -b feat/phase3-brainstorm origin/main
> cd ../fpl-autopilot-phase3
> # ... then start Claude Code in that directory, paste this prompt
> ```
>
> Confirm you've read the docs listed above. Then start the brainstorm by
> proposing the scope decomposition (your phase-1 task above) and asking
> the user one focused question at a time, per the superpowers
> brainstorming skill.

---

## Why this scope, why this shape (for the user)

- **Brainstorm-only mandate.** Phase 3 is "intentionally under-detailed" per
  `plan.md`. Sending an agent to "implement Phase 3" would put it in the same
  blocked state our main session was in. Forcing the first session to be
  pure design avoids that.
- **Cross-cutting spec + first-slice spec.** Most Phase 3 sub-slices share the
  same plumbing (which model? where does it run? how is data fed in?).
  Writing that once means every later slice doesn't re-litigate it.
- **No collision with the read-model slice.** This brainstorm doesn't touch
  `src/`. By the time the Phase 3 agent moves to implementation, the main
  agent's read-model work will have landed and the `my_team.free_transfers`
  field will be populated — which is one of the inputs a "why this transfer"
  LLM explanation would want.
- **B4 sacred.** The handoff explicitly tells the agent to default to
  **describe-only** AI (no decision changes) so the first slice doesn't get
  blocked on a `decision-engine.md` rewrite. If a later slice genuinely
  changes decisions, that gets its own doc-first ceremony.
