# Progress

## 2026-11-13: TreeGrow mining from Lab and exclusive input ownership

### Investigate
- Reproduced the reported stall in an isolated Minecraft 1.21.11 with the exact
  installed TreeGrow and Auto Defense. With released cursor: attack held, correct
  aim (dbAimWait=0), zero server leaf breaks, zero block-breaking progress. After
  ten seconds Unstuck cancelled the task. Auto Defense stayed idle without controls.
- Vanilla handleInputEvents requires a captured cursor for block breaking and
  otherwise cancels progress. The task/debug label alone does not establish mining.
- Post-child tool correction also allowed UnionClef to repeatedly equip an unsafe
  tool first, potentially resetting progress on every hand change.
- Initial test attempts were infrastructure failures during memory exhaustion and
  WSL timeouts, not product results. Recovered Docker only after checking all Lab
  jobs/character were paused; preserved DB/volumes. Owner closed Minecraft and
  authorized testing/installation. Voice/filter containers temporarily stopped
  for RAM and subsequently restored.

### Plan
- Preserve vanilla's single mining driver and all normal UI/server constraints.
  Allow the released-cursor gate only for the active, selected TreeGrow leaf-mining
  child, matching its exact target and safe hand. Never capture window focus.
- Choose the safe tool before native mining, keep equal-speed current tools stable.
  Preserve other mods, recovery/defense priority, manual stop, world and run budgets.

### Implement
- Added composable ModifyExpressionValue cursor gate, scoped stable tool selection
  before mining and typed read-only mining target access. Existing UnionClef
  position/block avoid predicates are checked too; no global input/defense override.
- Baseline real game failed with zero breaks under released cursor (exit 1).
  Final Gradle: 97 checks passed, remapJar and gametest compilation succeeded.
  The first unit snapshot omitted original acceptance metadata; restoring the
  source plan/catalog/docs fixed that harness error without altering test assertions.
- Five final isolated game runs exited 0: background (8 leaf breaks), foreground
  (12), shears/Silk Touch inventory (12), real defense takeover and deliverable JAR
  loaded as a dependency with original main classes removed (8 breaks). Four full
  cycles produced natural saplings and grown oaks, zero unsafe/leaf-block loot.
  Live guards checked menu/no attack/different target/foreign avoidance/restoration.
- Real takeover verified revocation and cancelled-root exclusion, not a complete
  Saruman combat/resume/publication cycle. The 108 separate runtime/preflight/Java/
  bridge/workflow checks passed and cover same-run/budget resumption and truthful
  workflow completion. Independent source/evidence review found no blocker.
- Installed only TreeGrow JAR f41e3b8710d15d39 after exclusive file check, with
  previous ed50500c backup outside mods. Auto Defense and Mage Home hashes unchanged.
  Adapter allowlists include the new exact hash; d40735000fcf1523 shim is compiled.
- Updated paused project 66b5a30bdb6945 sources and recorded maintenance event
  M-0000000000001286-7d4ed0e7107319014c582b826fe00c85e07f0caa5d9f0b525d8c68fd6ba7f3ef.
  Canonical acceptance/verification files, spec and job status were not changed.
  Full evidence/backup: Codex-Development-Lab/audit/mining-ownership-2026-11-13.
- Preserved a reproducible source/test patch and manifest in resources/Minecraft/
  patches. Applying it to a clean copy matched all 12 final source files byte-for-byte.
  Voice/filter services are running again; Lab is healthy/inactive, Minecraft closed,
  no model requests or posts in flight. Next explicit Start loads the new adapter.

## 2026-11-13: Keep the tree workflow pending during native scheduler recovery

### Investigate
- Latest private workflow c2dba6de entered test_3 and started TreeGrow. Native
  Unstuck Chain (priority 55) interrupted the user task (50). Auto Defense was
  not the interrupter; the saw_defense prerequisite marked this scheduler
  cancellation as failure. The later photo/publication steps correctly stayed pending.
- Native live read confirms scheduler interruptions with zero planted trees and
  remaining native budget. Idle Mob Defense may be selected with priority zero;
  treating every active foreign chain as busy would deadlock resumption.
- Follow-up mining observation: the retained DestroyBlockTask was stopped and
  its target 1010/72/29 was already air, with no current breaking predicates
  forbidding it. Held bow was observed while stopped, not during leaf harvesting.
  Subsequent native goal row 203 (source planner) confirmed one grown oak; owner
  goal row 202 and workflow row 27 stayed failed from Unstuck. This proves neither
  a general inability to break blocks nor completion of the private workflow.
  The old native worker PID 16164 required replacement; Stop/Start of its
  agent alone does not reload the parent bridge module or its cached Java shim.

### Plan
- Use native resumability plus actual scheduler selection/priority, without
  naming special-case recovery chains. Wait for higher-priority ownership and
  resume the exact same run after release; preserve budgets and cancellation.

### Implement
- Java snapshot exposes scheduler ownership/priority and native resumable state.
  Read cached scheduler priorities rather than invoking foreign getPriority,
  which can itself run survival/stuck detection. Low-priority idle chains do not
  block; unknown active ownership waits for a confirmed selection. Java also
  guards mutations against priority changes after Python's snapshot.
- Controller accepts scheduler interruptions without a saw_defense prerequisite,
  retains the goal as interrupted, and logs the actual control owner. No new
  tree, refreshed budget, blind command replay or success claim is introduced.
- Tests cover Unstuck release, unobserved Auto Defense, manual/expired/denied
  resume, low/unknown priority, stable task identity, receipt and budget. One
  older generic-failure fixture was corrected to use an actual failed outcome;
  its no-new-start/no-resume/no-growth assertions are preserved.
- Validation: 108 checks passed: 62 native-runtime, 6 preflight, 16 bridge,
  2 Java and 22 workflow checks. New Java contract also passed a read-only
  snapshot in the actual Minecraft JVM; no movement/cancel/plant/publication was invoked.
- Installation applied after the owner stopped the character: terminated only
  the verified native launcher tree (25728/16164), including its child workers.
  Lab remains healthy and inactive; Minecraft remains open. The next Start loads
  the updated Python modules and compiled Java shim 5c73c4cd822afafa. There are
  no active model requests, sending posts or pending IPC directive; the 22 sent
  posts and failed workflows remain history. A fresh owner command is required
  for the end-to-end planting/photo/publication retest; it has not been run.

## 2026-11-13: One public reply in chat and speech, without truncation

### Investigate
- PersonaAgent explicitly produced an 80-character chat summary and a separate
  700-character voice reply. Fish replaced texts longer than 300 characters with
  the first 250 and an invented feminine ending. Playback then cut audio at 20s.
- The actual Minecraft worker log contains repeated text-truncation messages and
  20-second clips. The local Fish /gradio_api/info metadata confirms that zero
  maximum tokens means no generation cap, with built-in iterative chunk length 200.

### Plan
- Generate one public text, send the same words to both transports, and divide
  Minecraft messages by their UTF-16 transport allowance without losing content.
- Keep existing voice references/emotions, explicit interruption and device-error
  handling. Remove fixed speech cuts; time the device watchdog from actual audio.

### Implement
- Persona output now has text/emotion/needs_action; there is no second summary.
  A shared public-speech path also handles native action commentary. Validate all
  outgoing chat parts before enqueueing and prevent chunks becoming commands.
- Requested replies no longer silently lose their voice when three entries are
  queued. Historical dual-field journal records remain readable; new records use text.
- Removed Fish text rewriting and the 500-token cap; preserve its existing native
  iterative generation. Playback sends the complete PCM and waits its measured
  duration plus device grace. No artificial 20-second slice remains.
- Validation: 176 checks passed: 67 role/agent, 6 public transport, 9 playback,
  6 Fish, 57 native dispatch, 16 bridge and 15 activity/memory. Synthetic long
  replies/Unicode chunks and a 50-second playback were checked without emitting
  sound or calling models. Fish metadata was read without generating audio.
- Installation: Lab is paused and no old native launcher remains. Updated native
  modules load on next Start; no Lab/Docker/Minecraft restart is needed. No public
  Minecraft message or Telegram post was submitted. Acoustic retest remains manual.

Previous completed work: [archive](archive/13-11-2026-gameplay-and-lab-completed-history.md).
