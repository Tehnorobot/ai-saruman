# Progress

Previous work: [exploration, voice and XP](archive/20-10-2026-exploration-voice-xp.md).

## 2026-10-20: Bounded combat pursuit and water-safe interruption

### Investigate
- Bridge reflex runs independently of the LLM, but native approaches can cross water and repeated unreachable targets keep defense active.
- Water rescue can capture and restore the same native combat approach; target water state is not published by entity scanning.
- Counted goals and background intent already have checkpoints. Other interrupted native goals need guarded, single-use resume.

### Plan
- Use short observed dry approach waypoints, pause immediately on combat water contact, never dive after a wet target or restore a combat route after rescue.
- Bound pursuit by time, lack of progress and distance; watch temporarily unreachable targets and re-engage on proximity/new confirmed damage.
- Resume saved work only after dry/idle and no threat, checking world, operator controls and newer commands. Keep friendly protection and no LLM combat dependency.

### Implement
- [x] Shared combat_pursuit policy: 24-second pursuit, 8 seconds without net approach, 12-block leash from the fight origin. Deferred targets stay alive in a bounded 30-second watch list; a new native-confirmed hit, visible melee contact or substantial dry-target movement can re-enable engagement. A wall-blocked close target does not wake defense every poll.
- [x] Native entity observations expose water/submersion and target visibility. Dry one-step approaches come from the observed connected BFS path; NavigationGuard revalidates adjacency/support/head clearance at dispatch and tags combat ownership. Failed route corridors and previously proposed waypoints are excluded. Hobby hunting cannot recreate a deferred chase through ordinary movement.
- [x] Water contact during combat preempts immediately, even with full air and an active native route. Rescue watches the interrupted target, clears combat ownership and never captures/restores the combat route. Ordinary safe surface traversal retains the existing breathing/grace behavior.
- [x] Ending a pursuit stops its owned native step once and waits for confirmed idle before releasing other work. Existing counted-tree/background checkpoints remain intact. Bridge-local native checkpoint covers other interrupted user root tasks; resume is single-use, dry/grounded/idle, same-world and gated by current operator/player intent and work-command generation. Stop/death/tool-disable/world switch/new command cancel stale work; unknown resume dispatch is not replayed.
- [x] Native navigation/collection guard snapshots preserve original absolute limits and observed collection progress on resume; expired or cancelled work cannot be revived. Checkpoint failures do not delay the urgent stop/defense path.
- [x] Gradio and get_game_status show pursuit marker, watch reasons and checkpoint metadata. Personality/system prompt explain shore-only attacks and defer semantics. No new shield/supply policy beyond the already implemented staff/XP/sword fallback; this turn implements the approved bounded pursuit/water/resume policy.
- [x] 369/369 unit/integration checks passed (.cache/pursuit-full-tests.log), including new pursuit, water strip, ranged shore attack, watch reactivation, native stop confirmation, checkpoint cancellation/single-use/limits and Gradio visibility regressions. Existing combat, protected creatures, crops, trees, queues, water, respawn and UI checks passed. Ruff changed modules/tests, F821 changed legacy files and git diff --check passed.
- [x] Windows guide updated. No live Minecraft actions, model calls, DB/Redis writes, process restart or Git commit/push performed. Apply with full Python restart, then Gradio Start; look for combat_pursuit_runtime=bounded-pursuit-v1 / the pursuit line in Gradio. Docker rebuild unnecessary. Actual in-world travel/physics remain to be checked after restart.
