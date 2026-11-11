# Prepared-world client launch

`quickplay.init.gradle` is an image-owned Gradle init script. It opens a world
that the supervisor has already cloned into a private game directory. It uses
Minecraft's existing `--quickPlaySingleplayer` option by default. An optional
image-owned helper handles dependencies that require the normal title-screen
lifecycle before a world opens. No project source patch or additional LLM role
is needed.

The runner supplies these process-scoped environment variables:

| Variable | Value |
|---|---|
| `LAB_WORLD_FIXTURE_MODE` | `quickplay`, `managed`, or `off` (default) |
| `LAB_WORLD_FIXTURE_NAME` | A lower-case save directory name, not a path |
| `LAB_WORLD_FIXTURE_GAME_DIR` | An absolute private game directory |
| `LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR` | Image-owned optional setup helper |

Example: with name `lab-fixture`, the world must already exist at
`<game-directory>/saves/lab-fixture/level.dat`. Template preparation and copying
belong to the supervisor. The init script does not create an empty placeholder,
grant commands, edit game rules, or accept the EULA.

Only Minecraft client launchers receive the arguments. For the Fabric development
launcher, `fabric.dli.env` must equal `client`; server and unit-test tasks remain
unchanged. Existing destination/quick-play arguments are replaced, while unrelated
arguments remain, including arguments supplied lazily through Gradle providers.

The laboratory also replaces `--username` and `--uuid` with one offline test
player derived from the private game directory. Every client task and restart
using that directory gets the same identity; a new scenario clone gets its own.
This prevents Loom's random default `Player###` from turning a persistence test
into a different-player test. Separate and `--option=value` arguments, including
lazy providers, are normalized to one identity. Access tokens and other launch
options are preserved. Managed harnesses, Fabric client GameTests, server tasks
and unit tests keep their own launch identity.

The adapter also updates public Loom run settings: Loom resets its working
directory inside the task action, after ordinary `doFirst` actions. Both working
directory and `--gameDir` select the private directory, preventing a prior run's
config or saves from becoming the new fixture.

`managed` disables this adapter when another harness owns world lifecycle. Actual
Fabric client GameTests are also detected by the presence of
`fabric.client.gametest`, including when its value is `false`. The script does
not also open a second world for those tests.

## Optional dependency preparation

The adapter reads `fabric.mod.json` from the actual client launch classpath.
An unused JAR in `libs`, a filename or an old project's requirements does not
select preparation. Currently the supported compatibility case is mod ID
`altoclef`, version `1.21.11-0.95.61` (UnionClef). Other projects retain native
Quick Play and do not load the helper or acquire UnionClef requirements.

For that case the adapter removes Quick Play arguments and adds the image-owned
client helper through `fabric.addMods`, preserving existing additions. Minecraft
shows its normal title screen. UnionClef's own title-screen mixin initializes
the dependency and its once-only guard. The helper rechecks actual Fabric Loader
metadata and observes a real task chain and SlotHandler before opening the saved
world through Minecraft's integrated-server loader. It does not synthesize
dependency events, write private guards or reuse another JVM's readiness.

The helper writes `client-dependency-preparation.json` in the private gameDir;
the supervisor retains it beside the scenario's original log before cleanup.
`WAITING`, `READY`, `WORLD_OPENING`, `WORLD_LOADED` and `UNAVAILABLE` describe
setup observations only. The receipt never supplies product PASS, changes
permissions or hides real JUnit failures. No fixed sleep establishes readiness.

Helper source and its reproducible Java 21 builder are in `dependency-bootstrap/`.
The builder compiles against a pinned SHA-256-verified Fabric Loader API. The
generated JAR is image-owned under `runtime/` and is not shipped in product JARs.

`LAB_WORLD_FIXTURE_LAUNCH` records launch configuration only. The receipt includes
the configured offline player's name and UUID for comparison between JVMs; it
does not establish the actual loaded player's identity. Actual world/player
readiness and every product assertion still require observations in the running
client. Merely passing a world name or finding `level.dat` never establishes PASS.

API verification: Minecraft 1.21.11/Yarn `1.21.11+build.3` exposes
`Main`'s `quickPlaySingleplayer` argument. `QuickPlay` dispatches it to the existing
single-player loader. Fabric API's client GameTest mixin plugin uses a presence
check for `fabric.client.gametest`. These interfaces were inspected in the local
version-pinned game JAR and Fabric API sources.
