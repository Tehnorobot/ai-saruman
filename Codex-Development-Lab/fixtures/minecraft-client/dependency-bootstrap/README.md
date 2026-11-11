# Per-process dependency preparation

This client-only helper is injected only when the launcher selects a supported
adapter from actual runtime mod metadata. It adds no dependency to other
projects. Compilation uses the pinned Fabric Loader API only; Minecraft methods
are resolved through Fabric's runtime mapping resolver.

The first adapter supports Minecraft 1.21.11 and the installed mod metadata
`altoclef` / `1.21.11-0.95.61`. Native QuickPlay is omitted for this launch.
Minecraft displays its normal title screen. UnionClef's own title-screen mixin
then initializes the real dependency and its own one-time guard. After a client
tick observes that screen, the task chain, and the slot handler, the helper calls
the real integrated-server loader to open the already cloned saved world.
It never publishes a synthetic UnionClef event or writes a private mixin field.

Launcher properties:

| Property | Value |
|---|---|
| `lab.client.setup.adapter` | `altoclef-title-1.21.11-0.95.61` |
| `lab.client.setup.world` | A lower-case save directory name |
| `lab.client.setup.output` | `<private-game-directory>/client-dependency-preparation.json` |

The helper is inactive without the adapter property. It verifies loaded metadata
again before registering its callback. GameTest-managed worlds do not use it.
Preparation writes JSON status and `LAB_CLIENT_DEPENDENCY_PREPARATION` log
records. `READY` means only that dependencies initialized; `WORLD_LOADED` also
records world/player presence. Product behavior still requires independent
scenario assertions.
Failure writes a full exception to stderr and an `UNAVAILABLE` receipt; it does
not report a product PASS or terminate an active Minecraft session.

Build with `python3 build.py --offline` after placing the pinned Loader JAR in
`../runtime/`. Without `--offline`, a missing compile API can be downloaded from
the pinned official Fabric Maven URL and checked against SHA-256. No project
source, mod compilation, or game launch is needed to build this helper.

Version-specific APIs were checked against the cached Yarn
`1.21.11+build.3` mappings and Minecraft bytecode:

- `MinecraftClient.createIntegratedServerLoader`: intermediary `method_41735`;
- public `IntegratedServerLoader.start(String, Runnable)`: `method_57784`;
- normal `TitleScreen`: intermediary `class_442`.
