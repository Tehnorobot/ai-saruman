# Progress

Previous work: [ranged defense and combat display](archive/20-10-2026-ranged-defense.md).

## 2026-10-20: Native combat mod implementation specification

### Investigate
- Operator requests a detailed handoff specification for GPT-6, not implementation or installation of a mod.
- Read current bridge combat/water ownership and prior progress. Checked official Fabric 1.21.11 key/tick and client-command examples, version/mappings notes and damage packet mapping.
- Preserve the described 32-block detection, F8, native melee/bow/shield, target policy, path/cover/dodge, retreat at 4 HP, food, permission-aware self supply and return of control.

### Plan
- Define a standalone native combat controller with no LLM/Python dependency in the action loop, bounded work, observable states and safe integration ownership.
- Provide a public versioned Java/JSON API, telemetry, lifecycle/rollback, exact target policy, water handling, equipment authority and concrete acceptance scenarios.

### Implement
- [x] Created docs/dev/SARUMAN_AUTO_DEFENSE_SPEC.md with 18 sections, API/status examples and 20 acceptance scenarios.
- [x] Distinguished client core, optional UnionClef/Magical Scepter adapters, capabilities and actual server-confirmed effects. Spec requires executable navigation/bow/shield/ownership, not placeholders; build and gameplay verification reported separately.
- [x] Checked section numbering, Markdown fences, JSON example and UTF-8; documentation diff whitespace checked. Runtime and settings are unchanged, so no gameplay tests or restart are needed for this documentation-only task.
- No mod built/installed, no game/Redis/DB mutations, no Git commit/push performed.
