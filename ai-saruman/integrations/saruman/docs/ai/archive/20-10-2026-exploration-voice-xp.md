# Progress

Previous work: [idle recovery](archive/20-10-2026-idle-recovery.md).

## 2026-10-20: Exploration connectors, staff XP and varied commentary

### Investigate
- Exploration rejects revisits/short moves needed to reach new frontiers. Shared visited squares can cover every reachable destination; exhausted searches persist for 15 minutes.
- Native staff checks total XP points. Operator now explicitly permits self XP commands.
- Background speech uses fixed strings. PostgreSQL/Alembic can store editable phrases and authored prehistory without model calls per action.
- Point 4 is discussion only; current bridge defense and friendly protection stay intact.

### Plan
- Allow observed dry connector walks, avoid recent arrivals and failed goals, preserve bounded passes and movement confirmation.
- Supply a modest XP reserve when a real staff lacks XP, verify through native component, back off unconfirmed commands.
- Seed an editable DB speech/lore catalogue, cache nonrepeating status-accurate comments on existing TTS pipeline.

### Implement
- [x] Exploration permits 1..4.5-block observed dry connectors when no novel frontier is reachable. Shared recent actual arrivals and failed destinations exclude immediate reversals; repeated reads preserve proposals/budget. A full autonomous maximum-budget pass can renew while retaining visit/arrival history. World changes reset shared background-search memory.
- [x] NativeWorldRules permits departure from the actual grounded partial-height slab/farmland cell only when native collision height matches feet Y and head space is clear. Destinations remain supported dry full blocks. Tests cover grounded/falling/height mismatch and no invented support.
- [x] Real staff XP shortage requests self `/experience add` points (100 minimum, cost*20 reserve capped at 10000), reserves state before dispatch and verifies through actual component readiness. Unknown delivery/denial backs off 60 seconds. Combat can request XP without waiting for a model; fallback melee remains available. No XP request without server mod, during death/water, or when enough XP already exists. Diagnostics in Gradio/get_scepter_status.
- [x] 54 status-specific phrases and 32 explicitly authored prehistory records; local nonrepeating cache, occasional peaceful lore, 12-second speech spacing with first combat comment exception. Native result semantics remain separate from fictional prehistory. Recent comments enter the model context and Gradio JSON; no extra model requests per action.
- [x] Alembic b7c8d9e0f1a2 adds action_voice_lines once, keeps operator edits, loads through existing StreamerDatabase. Applied only this new migration to local PostgreSQL after checking previous revision; read back all 86 records using the real loader. No live Redis/game/model mutations or runtime restart. Existing process uses its old loaded modules/catalogue until restart.
- [x] 345/345 unit/integration checks passed (.cache/exploration-voice-xp-full-tests.log), including water/combat/respawn/native controls, queues/trees/farming/Gradio. New speech/connector/migration checks also passed after formatting/import cleanup. Ruff changed modules and F821 changed legacy modules passed; git diff --check passed. Unrelated existing ORM forward-reference F821 warnings remain outside changed files.
- [x] Windows guide/personality updated. Point 4 remains a discussion: existing bridge reflex already scans nearby threats independently of tasks/LLM; recommend equipment preflight, one body owner, bounded pursuit and checkpoint resume to avoid starving player requests/hobbies. No new shield/equipment/pursuit policy enabled. User retains all Git commit/push operations.
- Apply runtime changes with full Python restart then Gradio Start; planner marker exploration-voice-xp-v1. Docker rebuild unnecessary. Live walking/XP casting still needs an in-world check by the user.
