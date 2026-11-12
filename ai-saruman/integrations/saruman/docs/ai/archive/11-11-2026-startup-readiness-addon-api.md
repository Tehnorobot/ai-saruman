# 2026-11-11 — Filter cold start and installed Auto Defense API

## Investigate

- Owner's startup failed filter model readiness and combined native addon API checks.
  Read actual service logs, /health, installed JAR metadata/hashes, javap contracts,
  current native adapter and read-only Java API. No game commands or LLM generation.
- Filter was still loading when TCP became available. It subsequently returned
  HTTP 200 with models_loaded=true; logs showed successful startup, not a model crash.
- Installed Auto Defense is addon 0.1.0, SHA-256 5e773d1e64f651943b5faf709aea3ef1458503a2a25910acd476ab6a457e25cb.
  Its API is get().state(), not the legacy getStatusJson(). Client package/mod ID,
  ownership and supply contracts also differ. TreeGrow and UnionClef match their pins.

## Plan

- Wait for real filter readiness on normal run, with an explicit configurable grace;
  retain immediate read-only Doctor checks and reject an unready filter.
- Add a separate pinned branch for the installed addon while preserving legacy
  support. Do not remove compatibility checks or install older JARs to hide failure.
- Use the addon's own supply controller; respect manual stop and prevent replay,
  foreign-task cancellation and game-world mutation from readiness probes.

## Implement

- Launcher waits for filter /health, default 180 seconds. Owner can set
  SARUMAN_FILTER_STARTUP_WAIT; 0 checks immediately. Both templates document it.
- Native preflight reads each loaded mod's Fabric origin/hash and the correct API.
  It reports the specific mismatch and accepts an idle TreeGrow current()==null.
  It never compiles the shim, enables defense or invokes a game task.
- ModGateway supports both verified Auto Defense client packages and contracts.
  New branch reads ownsControls/action/isEnabled/manualStop and state(). Only
  legacy branch accesses handover/bridge replay fields. Hash checks use actual
  Fabric mod IDs (auto_defense versus auto-defense).
- Native snapshot preserves all addon API facts, normalizes needs/owner for UI,
  and marks native supply ownership to suppress duplicate Python /give requests.
- A latched native Stop pauses automation until explicit Stop/Start. Acknowledging
  the event does not clear the addon's latch. Generic command receipts, world
  checks and exact-task cancellation remain intact.

## Validation

- 50 Python native-runtime/preflight tests passed, including added cold readiness,
  exact hash/API checks, native supplier ownership and manual-stop cases.
- 3 existing filter-readiness/environment-template/offline-doctor tests passed.
- Real Java shim compiled with --release 21. Isolated harness executed both addon
  and legacy contracts: reject unverified doubles, read-only snapshot, idempotent
  enable/disable, combat ownership, manual Stop acknowledgement, stale-world
  refusal, command receipt deduplication and cancellation of only the owned task.
- Live read-only snapshot in the owner's running Minecraft returned ready,
  in_world=true, addon-0.1, defense_enabled=false, defense_busy=false, native_supply=true.
  Detected 11 native item needs. Did not enable defense, start TreeGrow, issue
  /give, operate Mage Home or replace any JAR.
- Full native-Windows Doctor --live passed every check: dependencies, roles,
  existing Codex authorization, service ports, loaded filter models, open world,
  verified addon APIs and PostgreSQL authentication. No model response generated.
- Live combat, voice quality and planting remain the owner's next integration test.
  Existing legacy asyncio ResourceWarnings were observed without failing tests.

## Apply

Rerun scripts/start-saruman.ps1 from integrations/saruman. Restart an old Python
runtime first if it is still open; Stop/Start does not reload source. Minecraft,
JARs and Docker services need no restart for this compatibility fix.
The unrelated Mage Home specification edits and local private future-requirement
note are excluded from this change.
