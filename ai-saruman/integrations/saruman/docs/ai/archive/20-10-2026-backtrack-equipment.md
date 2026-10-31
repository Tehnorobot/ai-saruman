# Progress

Previous work: [bounded combat pursuit](archive/20-10-2026-bounded-combat-pursuit.md).

## 2026-10-20: Escape searched dead ends and bound optional equipment work

### Investigate
- Read-only live Redis/native inspection: agent alive and dry, collecting missing saplings; no global weapon/XP gate. Fresh native snapshot contained 109 walkable positions. Running bridge has not yet loaded bounded-pursuit-v1.
- Search trail rejects all revisits, including the only valid exit from a dead end. Per-purpose search budgets also survive large manual displacement.
- Weapon material acquisition/crafting starts outside the hobby job deadline; pending staff supply can delay selecting other work. Shield is not a prerequisite or implemented auto-supply gate.

### Plan
- Allow deliberate bounded backtracking along observed routes, mark exhausted branches so return cannot become A-B-A-B, share this state across search purposes and reset local pass after large relocation. Keep failures, dry support, budgets and native confirmation.
- Make equipment preparation optional, track native acquisition/crafting with a short deadline and retry backoff; pending command verification must not block unrelated hobbies. Respect Stop/player priority/water/combat.

### Implement
- [x] Search permits a deliberate backtrack when recent-visit exclusion blocks the only observed exit. Arrival pops the exhausted branch from the trail; a shared 60-second exhausted-position list prevents another activity immediately sending the character back. Movement budgets, failed exits, disabled movement and dry connected geometry still apply. Large relocation (>16 blocks since that activity's last scan) starts a new local background pass without resetting counted-tree resource/search limits.
- [x] Global background retreat/trail/exhausted state is shared across farm/hunt/tree/explore search purposes and cleared on world change. Regression covers farm retreat -> hunt chooses another branch, repeated-read idempotency, no immediate A-B-A-B, failed exit, budget and relocation.
- [x] Passive ensure_scepter(allow_crafting=False) cannot start native crafting. Pending self-give/XP verification no longer delays hobby selection. Missing sword/material acquisition and explicit ready-to-craft attempts are tracked separately with a 20-second deadline and 120-second retry backoff; no auto-acquisition/crafting starts when its stop tool is disabled.
- [x] Bridge publishes fresh native user-root observations during preparation. Only the observed matching root may be cancelled once; replaced/stale/unconfirmed roots are not overwritten. Staff crafting returns its actual Java task identity. Native idle must be observed before selecting other work; operator/player priority/death/water/combat retain their gates. Shield is not a prerequisite or newly introduced supply task.
- [x] Gradio exposes preparation phase, result, retry and search retreat memory; model status includes optional preparation details. Planner marker is search-backtrack-equipment-v1. Full Python restart then Gradio Start is required; also verify the earlier pursuit marker bounded-pursuit-v1. Docker rebuild unnecessary.
- [x] 379/379 unit/integration checks passed (.cache/backtrack-equipment-full-tests.log), including new search/gear regressions, passive craft/no weapon gate, no cancellation of replaced native work and all previous combat/water/respawn/trees/crops/queues/Gradio tests. Ruff changed modules/tests and F821 changed legacy modules passed; git diff --check passed.
- [x] Windows guide/tool docs updated. Only read-only live native/Redis inspection was performed; no live movement/inventory/model actions, DB/Redis writes, restarts or Git commit/push. New behavior still needs an in-world check after restart.
