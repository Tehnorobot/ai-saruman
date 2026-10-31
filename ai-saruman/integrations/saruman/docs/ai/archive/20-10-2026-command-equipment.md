# Progress

Previous work: [search backtracking/equipment scheduling](archive/20-10-2026-backtrack-equipment.md).

## 2026-10-20: Permission-aware equipment preflight

### Investigate
- Read-only native command dispatcher exposes give and experience in the current connection (method_2886); actual player XP is zero.
- Previous supply tried staff /give without checking permission, then staff materials/crafting; XP refill happened only after depletion, including during combat. Operator now requires advance command-based kit when commands are available, otherwise sword only.

### Plan
- Inspect the server-advertised command tree, scope requests to current world/player, grant only missing self sword/shield/configured staff (when supported), maintain an XP reserve. Verify actual inventory/XP and bound unconfirmed retries.
- Gate ordinary work briefly for required command preflight, retain emergency rescue/defense priority, remove combat-time XP commands and no-command staff crafting/material acquisition.
- With no commands, keep bounded ordinary sword acquisition and continue other life; expose policy/readiness in Gradio and update contradictory instructions.

### Implement
- [x] Added equipment_supply: server-advertised command aliases, self/world-scoped request ledger, inventory-confirmed sword/shield/configured staff, advance observed XP reserve. Only missing equipment is granted; unknown deliveries are reserved, confirmation wait is four seconds and unconfirmed retry is at least 60 seconds.
- [x] Without give/experience permissions only a sword is required; give-only grants a sword. No automatic staff recipe/material collection. Without server mod support full-command mode supplies vanilla sword/shield only. XP refill occurs in preparation, never inside cast/defense.
- [x] Autonomous preflight precedes saved goals and hobby continuation; rescue, death, emergency defense and player priority retain their gates. Ordinary sword acquisition keeps its existing 20-second ownership-aware cancellation and 120-second retry. Disabling ensure_scepter clears stale equipment-wait UI state.
- [x] Added shared equipment_supply schema/defaults, get_game_status/get_scepter_status telemetry and Gradio capability/readiness/XP diagnostics. Updated tool/prompt/personality/procedure/Windows instructions to the same policy.
- [x] Validation: 395 focused regression/integration tests passed, including command permission loss, unknown delivery, real inventory/XP confirmation, advance reserves, native death/defense gates, saved goals, Gradio callbacks, water rescue and respawn. Targeted Ruff and legacy touched-file F821 checks passed; Python AST, personality YAML and git diff --check passed.
- No live give/experience commands, game actions, database updates, restarts or Git mutations performed for verification. Read-only native inspection confirmed the current connection advertises give/experience and actual XP was zero.
- Apply with a full Python restart then Start; Gradio scheduler runtime marker: command-equipment-preflight-v1. Docker rebuild is not needed.
