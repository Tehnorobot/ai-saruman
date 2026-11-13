# TreeGrow background mining maintenance patch

`treegrow-background-mining.patch` applies to the TreeGrow 0.1.0 source used to
build SHA-256 `ed50500c43293f614c632b65f9c31c3a116a35c7759f16d547c392c3ae5708bf`.
It contains the production fix and executable client regressions. Original/new
source hashes and observed build/test results are in the adjacent JSON manifest.

The local maintained source is
`Codex-Development-Lab/projects/66b5a30bdb6945/workspace`.
Evidence and installation backup are in
`Codex-Development-Lab/audit/mining-ownership-2026-11-13`.

For another copy of the same source, verify the manifest's original hashes, then
apply the patch from that source directory:

```sh
git -c core.autocrlf=false apply --check /path/to/treegrow-background-mining.patch
git -c core.autocrlf=false apply /path/to/treegrow-background-mining.patch
```

Keep the original pinned UnionClef and its nested libraries. The regression-only
`libs/auto-defense-0.1.0.jar` must have SHA-256
`bdc144486e720b9a31a58544883593c7f0412e9a8da8262c6bf34f618d38f736`.
It is a local test dependency; TreeGrow's production metadata does not require
Auto Defense. User-provided third-party JARs are not embedded in this patch.

Build with the existing restricted runner and pinned Gradle/JDK21 dependencies:

```sh
gradle --offline test gametestClasses remapJar
gradle --offline runClientGameTest -PlabAcceptanceScenario=oak-acquire-cycle -PlabMiningOwnershipProbe -PlabMiningOwnershipMode=unlocked
```

Other regression modes: `foreground`, `silk`, `takeover`. Run game tests only with
existing EULA authorization. The maintenance harness must not set canonical
project acceptance statuses. The report in the audit directory distinguishes
real game checks from scheduler/protocol tests.

Deployment replaces only TreeGrow after Minecraft exits, preserves the previous
JAR outside `mods`, and updates Saruman's exact artifact allowlist. Do not remove
the allowlist or auto-enable the character while installing.
