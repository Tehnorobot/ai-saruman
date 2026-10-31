# Progress

Previous work: [native combat specification](archive/20-10-2026-native-combat-spec.md).

## 2026-10-20: Universal Auto Defense and separate Saruman integration

### Investigate
- User wants a distributable general-purpose mod and Saruman to adapt to its public API, instead of a bespoke character mod.
- Prior specification mixed core combat, character policy, Python supply implementation and required project-specific adapters.
- No new JAR/API has been supplied or tested; removing the current combat runtime would be premature.

### Plan
- Preserve detailed native combat requirements in a canonical universal mod specification: single base JAR, standalone vanilla features, no Python/UnionClef dependency, optional addons, profile policy and stable versioned API.
- Replace the original character-specific specification with a profile/adapter/migration plan. Define one owner for combat, rescue and supply, explicit backend negotiation and safe fallback only after confirmed release.

### Implement
- [x] Created docs/dev/AUTO_DEFENSE_SPEC.md: 18 sections, 24 acceptance cases, universal API/package/commands, independent packaging and local settings UI, optional providers, capability negotiation, explicit command-supply opt-in and world-scoped idempotent mutations.
- [x] Reworked docs/dev/SARUMAN_AUTO_DEFENSE_SPEC.md into eight integration sections: strict character profile, current/native/auto backends, Java-side arbitration, native water/supply ownership, Gradio/events/speech, loss of API, staged rollout and rollback.
- [x] Validated section numbers, JSON, Markdown fences, UTF-8, local cross-links and removal of bespoke API/mod identifiers from the universal specification. Whitespace checks passed.
- Documentation-only: current runtime, environment and enabled combat behavior unchanged; no JAR installed/built, no gameplay/Redis/DB mutations, no Git commit/push. No gameplay test or restart required for these document edits.

## 2026-10-20: Full native combat movement feasibility

### Investigate
- Operator requires the new mod to own all combat movement, defense and retreat, with advanced PvP-style movement; UnionClef must not constrain it.
- Read-only inspected the installed unionclef-1.21.11-0.95.61.jar. PauseCommand stops the user chain and disables TaskRunner, stopTasks calls Nav.cancelAll, InputControls exposes releaseAll. AltoClef.onClientTick still calls input pre/post and weapon-selection hooks outside TaskRunner.tick, so disabling tasks alone does not establish exclusive control.
- Extracted selected class files to ignored .cache for javap inspection only. No game methods changing state were invoked and no new mod has been supplied for compatibility testing.

### Plan
- Require native combat movement in the Saruman profile and treat UnionClef only as a noncombat task backend while the new mod owns combat. Require JVM-side isolation of task/path/input/slot writers, including late cleanup.
- Keep optional external navigation in the universal API for other users, without making it a requirement of the base mod. Define a version-specific compatibility addon if existing public methods cannot enforce ownership.

### Implement
- [x] Updated both specifications with complete native combat movement ownership, advanced local distance/strafe/dodge/sprint behavior, native_combat_movement capability and acceptance cases 25-26.
- [x] Documented the inspected UnionClef entry points and remaining isolation/restore verification, without claiming proven JAR-to-JAR compatibility or top-PvP quality.
- [x] Documentation numbering, links, JSON and whitespace checked. Runtime remains unchanged; no live ownership handoff, input, inventory changes, Git commit or push.
