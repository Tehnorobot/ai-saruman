# Progress

Previous work: [scepter and water v1](archive/20-10-2026-scepter-and-water-v1.md).

## 2026-10-20: Water recovery, sword fallback and urgent combat

### Investigate
- Surface contact cancelled every task; rescue required a standing-cell route, with no movement when a shore was outside the local snapshot.
- Native @stop disables all automation. Native MobDefenseChain flees; Python defense waited for the chat model and refused low-health melee.
- FabricLoader checked only the client mod, not server payload support. Native jars and Fabric networking docs checked.

### Plan
- Allow surface swimming with continuous buoyancy. Submersion preempts once; observed swimming corridors find shore or new water frontier. Resume saved goals.
- Detect server channel support; obtain/prefer sword when magic unavailable, checking sweep clearance.
- Bridge owns urgent combat, preempts native work, approaches permitted targets, honors Start/Stop and shared Tool Controls; no low-health refusal during defense.

### Implement
- [x] WaterSafety v2 allows surface travel with buoyancy. Idle in water, four seconds without horizontal progress, two seconds submerged, or half air trigger rescue. One stop per phase; direct MOVE_FORWARD/JUMP follows observed swim corridors, not an unconstrained @goto. No shore yet means exploring observed water. Rejected aiming releases forward input.
- [x] Swimming starts from fractional/submerged feet, supports farmland/slab banks, and requires grounded dry arrival. Stalled waypoints are excluded. Same native task identity resumes at most twice, only in the same world. Stop/death discard pending native resumption and release owned inputs.
- [x] Tree checkpoints and background queue survive rescue. Confirmed count, target positions, dose consumption and explicit resource budgets persist; world identity gates automatic continuation. Interruption IDs remain visible after fast rescue/combat so an LLM response cannot miss the interruption.
- [x] Magical Scepter checks the connected server's Fabric attack channel. Missing support skips staff give/craft, reports spells unavailable, and supplies a vanilla sword through registered obtain_item. Sword/axe/pickaxe preference and sweep clearance protect peaceful entities; urgent defense can use a verified empty hand if necessary.
- [x] CombatReflex in the bridge checks every 0.5 s, independent of model/chat events. Nearby hostiles or native-confirmed attackers preempt ordinary native work; defense closes and attacks without the optional-hunt low-health refusal. Native fleeing is suppressed while this controller runs and restored at Stop/tool disable/shutdown. UUID/type protection, damage proof TTL and player range remain enforced.
- [x] Shared Tool Controls are published on registration, toggles and Enable/Disable All. The bridge is the sole urgent combat owner; defend_self reads its result, chat does not duplicate attacks/protection or clear its movement ownership. Deferred goals resume after verified idle. Fixed failed-route record shape at the real approach-planner boundary; covered by an integration regression.
- [x] Gradio shows swimming/air, rescue, sword fallback and runtime versions water-safety-v2 / reflex-v2. Persona, prompts, combat procedures and Windows guide match the implementation.
- [x] 323/323 focused and integration tests passed: water idle/stall/short dips/ceiling/swim frontier/failed hop/land/resume/world/budgets/retry cap, absent server mod/sword/sweep/empty hand, urgent low-health combat/approach/protected targets/player range/shared controls, plus existing trees/crops/respawn/queues/Gradio/navigation. Ruff targeted modules, F821 integration checks and AST/YAML validation passed.
- Read-only live gateway confirmed water/air/ground getters and server scepter channel support. An observed swimming waypoint was calculated in 0.92 s without moving the character. No live movement, attacks, /give, crafting, model calls, service restarts or git commit/push were performed for verification.
- Apply by fully restarting Python, then Start in Gradio; Docker rebuild is unnecessary. Live water escape and battle still need verification with the new process. Previous completed work was archived byte-for-byte in the linked file.
