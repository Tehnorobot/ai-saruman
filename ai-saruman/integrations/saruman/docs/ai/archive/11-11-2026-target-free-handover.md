# 2026-11-11 — Target-free Auto Defense handover

## Investigate

User observed the character standing indefinitely with a priority-60 Auto Defense
ControlGraceTask. Read-only live diagnostics confirmed enabled=true, target=null,
action=IDLE, ownsControls=true, no threat/rescue, health 20 and an inactive user
chain. Several mobs were 19 blocks below the player; the existing maxDrop=3 safety
policy correctly refused the direct route. The refusal repeated indefinitely.

An independent read-only source review found: unsafe refusal was not retained;
RouteControl retained only one excluded UUID; empty/vanished-target reviews could
reinstall a grace task after release. The Lab project was USER_PAUSED at round 82.

## Plan

Fix the addon, preserve fall/lava safety and native 60-tick handover, keep ordinary
tasks intact. Build an isolated snapshot through the restricted runner; no new host
mounts, external model calls, EULA acceptance or Minecraft execution. Preserve
the paused project's source state/history and record the maintenance diagnosis.

## Implement

- Source fix: per-UUID timed exclusions with per-target reasons; exclusions survive
  an empty observation/target change; full session reset clears them. Candidate
  checks use constant-time per-key expiry instead of scanning the whole map.
- Unsafe refusal records exclusion before dropping the target. Empty and vanished
  candidate reviews do not call dropTarget when the target is already null.
- Added ten regressions, registered in addonUnitClasses, including actual compiled
  AutoCombat wiring, four unsafe candidates and exact 60th empty-tick expiry.
- Restricted Gradle build passed 52 selected policy/contract tests and remapJar.
  Added generated SHA-256 6943228ddd690a24b5aad2e98bccb36e798c15b96c0a57a419979e42eeabe4ce
  to Saruman's pinned compatibility checks. Existing old hashes remain supported
  for rollback. Adapter Java harness and four native preflight tests passed.
- Applied the same reviewed four-file patch to the paused Lab workspace under its
  project lock after verifying original source hashes. Kept all acceptance files
  unchanged. Full source/test/log report remains under the ignored Lab audit path
  audit/auto-defense-handover-2026-11-11. No Lab files are committed/pushed.
- Appended full maintenance memory record
  M-0000000000003588-9c3ffce01f7077cd56728b55843ec8e2d751423c1a031b84857b018d6d68bfd5.
  The record links full diagnostics/results and explicitly leaves gameplay pending;
  no product requirement was relabelled PASS and no graph/status reset occurred.
- Owner authorized replacement after closing Minecraft. Confirmed closed gateway
  and an exclusive JAR handle; backed up the old 5e773d1e... artifact, then atomically
  replaced only .minecraft/mods/auto-defense-0.1.0.jar and verified both hashes.
  Backup: Codex-Development-Lab/audit/auto-defense-handover-2026-11-11/
  installed-auto-defense-before-handover.jar. Other mods/worlds/configs untouched.

## Validation boundary / next check

The new JAR was compiled/remapped and its logic/wiring tested, but sustained real
movement after handover is not claimed yet. Owner must reopen Minecraft/world,
restart the Saruman Python process, press Start and verify ordinary movement or
TreeGrow continues once unsafe targets are rejected. A reachable genuine threat
must still activate defense. Mage Home stays disabled.

An explicit exclusionTicks=0 or shorter than handover is not silently clamped;
such configurations can still need a separate provisional-target suitability fix.
The reproduced default case with 200-tick exclusions and native 60-tick handover
is covered. No additional fixed pause or new agent was added to the product.
