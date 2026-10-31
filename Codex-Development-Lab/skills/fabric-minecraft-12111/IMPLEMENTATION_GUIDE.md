# Universal Fabric 1.21.11 engineering notes

This document contains **implementation guidance only**, not requirements for any particular mod. `ТЗ.md` controls features, behavior, thresholds, supported integrations and acceptance criteria.

## Build and mappings

- Target Minecraft Java 1.21.11. Java 21 is the baseline toolchain; choose pinned, mutually compatible Fabric Loader, Fabric API, Loom, Gradle and mappings based on official metadata and the selected template.
- Do not mix Yarn, Mojang and intermediary names without the appropriate mapping/remapping layer. Verify the actual 1.21.11 signature before calling a method or writing a mixin target.
- Provide a normal Fabric metadata file and a reproducible remapped production JAR. Do not mistake a development JAR for a release artifact.
- Use the client/server environment distinctions required by the feature. A client-only mod cannot unilaterally grant server permissions or bypass authoritative game rules.

## Game lifecycle and interactions

- Register callbacks in the appropriate phase. Handle world unload, disconnect, death, dimension changes and re-entry for any feature that keeps entity or world references.
- Avoid blocking the game tick/render thread with sleeps, long path searches, file/network I/O or other expensive operations. Any background computation that relies on game state must use safe snapshots and revalidate before applying its result.
- For features that programmatically control the player, coordinate movement, view direction, inventory selection and attack/use actions; release only controls actually owned by the mod on cancellation or world changes.
- Use normal in-game item actions and cooldowns. Check relevant client/server state before claiming an item use, attack, inventory change or command succeeded.
- Do not issue server commands that the player lacks permission to run. Confirm outcomes from actual game state, not solely from sending chat/command text.
- Only if required by `ТЗ.md`, account for collisions, hazards, fluids, air supply and route validity. No default combat or water policy is implied here.

## Compatibility

- Treat installed mods as optional unless `ТЗ.md` requires them. Avoid assumptions about internal APIs of third-party JARs; inspect the exact version and test compatibility.
- If multiple mods affect the same game controls, define a single effective owner at a time and a safe handover. See the optional `minecraft-mod-compatibility` Skill.

## Verification

- Confirm Gradle resolves the pinned dependencies and compiles/remaps the project. If dependencies cannot be fetched, report the blocker instead of inventing an artifact.
- Unit tests are useful for isolated logic. Real movement, item use, GUI interaction and world effects require a running game/client or a relevant supported game-test environment.
- Do not silently enable EULA-bound testing without the user's authorization. Report exactly which checks were executed and which remain untested.
