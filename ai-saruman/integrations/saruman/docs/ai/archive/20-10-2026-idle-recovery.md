# Progress

Previous work: [water and combat v2](archive/20-10-2026-water-combat-v2.md).

## 2026-10-20: Idle after an uncertain tree interaction

### Investigate
- Live read-only Redis: agent_enabled=true, bridge water-safety-v2/reflex-v2 fresh, alive=true, native idle, no water or threat. Autonomy enabled=true but paused=true with automatic interaction-unconfirmed reason; last selection trees. A later death overwrote the detailed result but kept the pause.
- Autonomy globally paused all activities on a single unknown/error; saved-goal errors used the same global stop. tend_trees also mapped safe pre-click rejections/needs_move to unknown interaction.

### Plan
- End/cool down the failed activity and continue queued work after a bridge observation grace period. Never follow a continuation attached to an uncertain result; preserve operator Stop/wait.
- Block only a failed counted goal, keeping positions/count/budget. Retain bounded issue diagnostics across death and later successes, visible in Gradio.
- Preserve planting status meaning and provide a real observed approach after needs_move.

### Implement
- [x] Autonomy no longer sets a global pause on ordinary unknown/error results. It ends/cools down that activity, drops its unconfirmed next_action, waits 2.5 s for bridge observations, then serves other queued work. Existing fresh world/inventory checks run before a later attempt; native busy/stop-failed, water, combat, player priority and explicit operator Stop/wait still gate actions.
- [x] Saved-goal tool/continuation errors block only that checkpoint, preserving target coordinates, config, resource consumption and progress. Matching deferred copies cannot silently revive the erroneous continuation. Disabled saved-goal tools also release unrelated hobbies.
- [x] tend_trees distinguishes pre-click rejection, missing sapling, needs_move and genuinely unconfirmed placement. needs_move returns a real observed approach if one exists; safe rejection becomes blocked, not unknown interaction.
- [x] Up to 20 recent issue records retain activity/tool/arguments/original result even when death cleanup or another activity overwrites last_result. Gradio displays the latest issue and full JSON; planner runtime marker is activity-recovery-v1.
- [x] 332/332 targeted/integration tests passed, including the observed trees-unknown -> queued exploration regression, generic failure -> other hobby, saved-goal failure/budget retention, disabled tools, no unconfirmed continuation replay, operator wait preservation and Gradio diagnostics. Existing water/combat/respawn/queues/crops/navigation/tree checks also pass. Ruff, F821, AST six changed Python files and git diff --check passed.
- Windows guide updated. No live mutations, chat/model calls, process restarts or Git commit/push. The current process still contains the old global-pause handler: apply with a full Python restart, then Start in Gradio; verify planner activity-recovery-v1. Docker rebuild is unnecessary.
