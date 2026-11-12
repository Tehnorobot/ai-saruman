# 2026-11-11 — Preserve TreeGrow after defense handover

## Investigate

The owner requested one tree and confirmed no Stop/F8 or replacement request.
TreeGrow's sapling acquisition was interrupted by Auto Defense at 16:38:08. Four
unsafe targets below the player were excluded correctly by the prior fix. At
16:38:15 UnionClef naturally finished the terminal tree task and disabled its runner.
The generic `Stopped` log was not evidence of manual cancellation.

CombatSession still claimed the borrowed runner after releaseAfterBudget. Its
ordinary shutdown was misclassified as manual Stop, which caused the Python
dispatcher to cancel rather than resume the same TreeGrow run. An independent
read-only review of source and pinned UnionClef bytecode confirmed this chain.
The rescue handover had the same stale-claim defect. The final Python reason
`Player priority` additionally obscured the actual cancellation source.

## Plan

Relinquish the runner claim whenever combat hands over control; retain real Stop
handling and unsafe-route refusal. Preserve task identity/progress and acceptance
history. Fix misleading cancellation reasons without introducing a second AI or
automatic replay of cancelled user work. Build only in the restricted runner.

## Implement

- AutoCombat now unconditionally releases its CombatSession runner claim after
  normal handover and dependency rescue, including repeated surrender ticks.
  Foreign tasks remain running. Combat acquisition/manual Stop remain unchanged.
- Added two checks of every normal compiled handover path. Before the fix both
  failed; after it all 61 selected Gradle tests passed and remapJar succeeded.
- GoalStore.cancel accepts an optional reason. Runtime records native versus
  automation Stop and preserves the cancellation reason while waiting for defense.
  Player priority remains specific to background-goal preemption.
- 51 Python runtime/preflight checks passed. After the new artifact pin, five
  native Java-contract/preflight checks passed; the real adapter was precompiled.
- New artifact hash: 15af6beddd33e4388812a4502f629fdf08d73dd711017c3950612658bb094385.
  Both Python and Java compatibility checks retain all previous supported hashes.
- Reviewed source applied under the paused project lock. Acceptance files, graph
  and job status remain unchanged. Full diagnostics, before/after reports and JAR
  are local in Lab audit/auto-defense-tree-interruption-2026-11-11, not committed.
  Maintenance record:
  M-0000000000003589-421b2167710e17214d5f3be6afec7666571ac3365a21a63fd51ee544fd26856f.

## Validation boundary

The owner authorized replacement after closing Minecraft. Verified saved-world
shutdown, closed gateway and an exclusive JAR handle, then atomically replaced
only .minecraft/mods/auto-defense-0.1.0.jar. Verified new and backup SHA-256 hashes.
The prior 6943228d artifact is retained in the audit directory as
installed-auto-defense-before-tree-resume.jar; installation.json records the
operation. No game commands, LLM requests, service restarts, world modifications
or automatic goal replay were performed. Physical planting/resumption and actual
Stop still need a real-client retest after Minecraft/Saruman restart. Mage Home
remains disabled. Brief safety preemption on initial selection of an unsafe
target can still occur before that target is excluded.
