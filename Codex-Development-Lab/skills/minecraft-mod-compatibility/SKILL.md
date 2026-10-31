---
name: minecraft-mod-compatibility
description: Resolve required interoperability between Minecraft mods, especially when more than one mod controls movement, camera, inventory, tasks or inputs.
---

# Minecraft mod compatibility

**Activate only if `ТЗ.md` requires compatibility with other mods or the implementation actually encounters an inter-mod conflict.** This Skill adds no features to the project's specification.

- Identify the exact installed Minecraft, Fabric Loader and third-party mod versions, their exposed APIs and relevant runtime behavior. Do not assume two versions have interchangeable classes or hooks.
- Prefer supported integration or coordination points before version-specific mixins. Do not silently modify or replace another mod's JAR.
- If two mods may control the same player, establish exclusive ownership for each conflicting channel: movement, camera, sprint/jump, selected slot and attack/use. A visual `paused` flag is insufficient when the other mod still writes inputs.
- Plan an explicit handover: suspend conflicting actions; verify old input is released; allow the new owner to operate; stop and release its actions before restoring the previous owner. Prevent delayed callbacks from an old owner changing new state.
- Distinguish **restart a saved task request** from **resume an internal execution state**. Do not promise resume unless the installed mod offers a verified mechanism. Replay only requests that remain safe and relevant; handle interruption, death, disconnection, world changes and manual stop.
- For AutoClef/UnionClef specifically, if requested by `ТЗ.md`, inspect the actual installed version's task cancellation/pause, navigation, combat and input hooks. Do not assume that stopping a task also suspends every background controller. Treat exact class and command names as version-dependent until checked.
- Verify handover inside a real game session with both mods installed. A successful compile against one mod does not establish conflict-free runtime behavior.
