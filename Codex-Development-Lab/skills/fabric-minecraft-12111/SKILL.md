---
name: fabric-minecraft-12111
description: Build and debug Minecraft Java Fabric 1.21.11 mods using version-correct APIs, reproducible builds and genuine in-game behavior.
---

# Fabric 1.21.11

Use this Skill only when a project's specification targets Minecraft Java Fabric **1.21.11**. For other versions, establish their own compatible toolchain and mappings rather than copying 1.21.11 assumptions.

1. Read `IMPLEMENTATION_GUIDE.md` in this directory for **generic** version-specific engineering precautions. It is not a feature specification.
2. Honor the actual `ТЗ.md`: do not add combat, farming, navigation, AI controllers, hotkeys or third-party integrations unless the project asks for them.
3. Use Java 21 and compatible, explicitly pinned Minecraft/Fabric dependencies. Verify mappings against the exact configured game version.
4. Implement interactions using ordinary game/client/server APIs, respecting authoritative game rules; do not claim a key press or packet was equivalent to an observed result.
5. Distinguish a successful production-JAR build from gameplay verification. Require actual game execution to claim a behavior works in-game.
6. Do not automatically accept the Minecraft EULA. Run EULA-dependent test tasks only when already authorized by the platform configuration.
