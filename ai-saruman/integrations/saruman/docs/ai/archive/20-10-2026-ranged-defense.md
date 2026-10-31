# Progress

Previous work: [permission-aware equipment](archive/20-10-2026-command-equipment.md).

## 2026-10-20: Ranged defense and visible combat

### Investigate
- Defense acquires new hostiles only within eight blocks; watched targets suppress all attacks until melee contact. Native selectHotbar only sets the local slot; the mod attack callback does not synchronize that slot with the server. Exact melee dispatch has no hand swing.
- Read-only inventory confirms configured base staff and XP; its native ray range is eight. Native/Python aim calculations agree. No hostile currently tracked; the reported encounter cannot be replayed live.

### Plan
- Acquire hostiles earlier; preserve player evidence, protected creatures, dry navigation and pursuit limits.
- Synchronize selected slot before a mod attack; let watched targets receive a verified in-range spell without retrying failed navigation.
- Show target/distance/result in local Minecraft action bar and Gradio, retain spell diagnostics, restore melee swing.

### Implement
- [x] Native selected-slot packet explicitly precedes the mod attack callback; normal exact melee attacks turn toward the target and swing the hand. Attack/spell attempts are separately retained without treating request acceptance as damage.
- [x] Acquire hostiles up to 16 blocks, confirmed mob attackers up to 24 (players still 12 with native proof). Stop owned approaches at the actual available spell range; skip initial protection cooldown for distant targets. Dry shoreline steps are allowed with a ranged staff, never swim/chase water routes. Route watch can permit a visible in-range spell check every two seconds without reopening failed navigation.
- [x] Added local action-bar target type/UUID, distance, state and observed damage; HUD errors do not stop combat. Gradio and game tools expose combat_attack/scepter_attempt, including rejected casts. Added shared schema/defaults and updated the Windows/procedure instructions. Runtime marker: ranged-defense-v2.
- [x] Validation: 407 regression/integration tests passed, covering ranged slot ordering, earlier skeleton engagement, distant drowned proof, shoreline-only approaches, staff-range stopping, watched ranged attacks, exact melee swing, protected targets, HUD failures, Gradio, rescue and respawn. Targeted Ruff, touched legacy F821 checks, AST and git diff --check passed.
- No live attacks, inventory/XP commands, world changes, Redis writes, process restarts or Git mutations performed. Native methods and inventory were inspected read-only; no current hostile was available for a real encounter. Full Python restart then Start is required; Docker rebuild is not needed.
