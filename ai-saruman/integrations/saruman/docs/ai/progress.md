# Progress

## 2026-11-12: Recover player replies and separate voice-chat playback from the microphone

### Investigate
- STT recorded the nearby player's speech, while the persona MiniMax request failed
  schema validation. RoleClient then blocked every later request at that profile
  revision. ActionAgent continued independently, explaining movement without replies.
- The installed voice-chat client used CABLE Input as its speaker and CABLE Output
  as its microphone. Received player audio therefore entered the cable intended for
  Saruman's TTS and could be transmitted back as echo. The actual default speaker
  is Headphones (WH-1000XM4); the last generated TTS clip had played to CABLE Input.
- A persona worker error also marked the whole request as error, losing observation
  of an already accepted native goal. Successful replies did not clear the error.

### Plan
- Correct a failed persona output once with its exact schema issue and previous
  output. Account for each paid attempt, without executing tools or publishing it.
- Let new requests recover after format/transport failures; keep configuration/auth
  failures explicit. Retain goal observation and reject obsolete speech outcomes.
- Move only received voice-chat audio to the headphones after the owner closes
  Minecraft; retain the TTS-to-CABLE-to-microphone route.

### Implement
- Added sanitized schema-path/rule diagnostics and one targeted persona correction.
  Invalid outputs and transient HTTP/network errors do not latch the role forever;
  no blind replay of game tools, old player requests or public messages occurs.
- Failed speech no longer orphans accepted goals. Track role failures separately,
  clear only the recovered role and discard stale failures as well as stale replies.
- Validation: 71 role/voice checks passed, including usage on both correction calls,
  recovery on the next message, hidden unexpected tool fields, independent game
  completion, obsolete errors and preservation of an unresolved other-role error.
- The correction shares the configured per-response monotonic time budget; it does
  not silently double it and cause a valid late reply to be discarded downstream.
- Real MiniMax synthetic UTF-8 probe returned valid chat/voice JSON on the configured
  high profile; 1854 tokens recorded. It was not published or played. An earlier
  shell-encoding probe passed format only and is not treated as a semantic check.
- Deployment and the speaker change are prepared, not yet applied to the active
  game process. Evidence: .saruman/audit/voice-recovery-2026-11-12/.

## 2026-11-12: Restore private workflow visibility and the Minecraft dispatcher

### Investigate
- MineBridge died before entering its reconnect handler: BridgeHealth passed keyword
  arguments to RedisDict.update, which accepts a mapping. Dictionary-only tests
  missed the production collection contract; the accepted tree goal stayed queued.
- Maintenance timestamps were later than new commands after wall-clock rollback.
  Sorting the private chat by timestamps hid the current five-step workflow.
- One previous attempt failed on a Windows sharing conflict during command.json
  replacement. A historical native dispatcher lease also remained valid five hours
  into the local future, preventing a new dispatcher from observing any goals.

### Plan
- Test with the real Redis collection and nested proxies; preserve chronological
  insertion order, exact command identities, atomic files and cancelled outcomes.
- Reclaim only a proven dead process's dispatcher lease; retain live and legacy
  unknown owners. Never rewrite a failed/cancelled goal as success or replay it.

### Implement
- Fixed Redis updates, added insertion-order private history and brief retries for
  Windows sharing errors only. Original timestamps, old JSON and task IDs remain.
- Dispatcher leases now record their process ID and reuse the existing read-only
  process check to recover a dead owner after clock rollback without stealing a
  live owner's lease. Legacy leases remain conservative.
- Validation: 63 bridge/dispatcher checks passed; 121 Lab checks passed and one
  existing Windows symlink check skipped. Includes clock rollback, real RedisDict,
  live/dead/unknown lease owners and transient/permanent file permission failures.
- Installed with project-local SQLite backups and no model/Telegram send in flight.
  Removed only the unchanged legacy orphan lease verified against its backup.
  The bridge resumed fresh observations. The old workflow subsequently recorded a
  cancelled game action; its result and remaining unexecuted publication are kept,
  not replayed. At the last probe Minecraft had no open world/player observation.
- Maintenance evidence: .saruman/audit/control-workflow-recovery-2026-11-12/.

## 2026-11-12: Standalone Mage Home finalization handoff

### Investigate
- Compared the owner's installed round-10 JAR with the current paused Mage Home
  workspace, round-12 acceptance/checkpoint results and manual fix records.
- Separated product placement failures from unexecuted fixture checks. Confirmed
  that title-screen dependency initialization and automatic world entry belong to
  the Lab fixture infrastructure, not to the addon or Gradle scenario flag alone.

### Plan
- Provide one self-contained Russian Markdown with the complete current spec,
  approved bounded-repair clarification, observed failures, existing fixes and
  independent development-client instructions for another model.

### Implement
- Added docs/mod-development/mage-home/MAGE_HOME_FINALIZATION_HANDOFF_RU.md.
  Included version/hash provenance, source/dependency transfer requirements,
  code map, fixture portability, isolated run commands and completion evidence.
- Preserved the owner's edited original spec, active workspace, installed JAR
  and historical acceptance. No Minecraft run, LLM call or deployment performed.
- Validation: embedded all eight original spec sections; checked UTF-8, fenced
  examples and referenced source/fixture paths. Product status remains unverified
  for a complete house; this documentation does not claim a new gameplay PASS.

## 2026-11-12: Target exclusion crash and disconnected bridge recovery

### Investigate
- Owner crash report showed KillEntityTask.toDebugString dereferencing target=null.
  Independent review traced the producer: Auto Defense dropped an unreachable target
  but returned RouteOutcome.NONE, allowing same-tick weapon selection to create the
  invalid task. A second exclusion branch also continued into threat selection.
- After the game exited, an unguarded getGoalRotation read killed MineBridge's updater.
  Its process heartbeat continued publishing a cached in-world combat status. Cleanup
  also skipped callback/transport closure after a JVM error. The old ingame loop could
  wait inside the old connection rather than rebuilding callbacks and native guards.

### Plan
- Fix the addon control flow, retain target-free handover and automatic defense, and
  reproduce the exact crash in an isolated client. Do not modify UnionClef binaries
  or merely make debug output swallow the invalid entity task.
- Rebuild failed bridge connections; preserve durable intentions/receipts and manual
  Stop. Check native observation freshness separately from the Python heartbeat.

### Implement
- Auto Defense returns terminal EXCLUDED after route-budget exclusion and exits the
  second exclusion tick before further threat/weapon decisions. Isolated pre-fix client
  reproduced the exact NPE; another old-code run failed on post-drop threat selection.
  Both fixed game branches passed all six assertions, kept defense enabled and acquired
  a later valid hostile. All 71 selected route/weapon/handover unit checks and remapJar
  passed. Test-only mixin counters observe entry/return without rewriting decisions.
- Applied source/tests under the paused Auto Defense project lock. Canonical acceptance
  and round history unchanged. Maintenance record:
  M-0000000000003591-0f16ea751ec24151f353cbac96910b1cc594e63ba43b22f9a6e113686dd9649b.
- MineBridge now reports updater failures and leaves the old connection for rebuilding;
  liveness is read before helpers that deliberately suppress unavailable observations.
  Adapter construction failure retries connection setup rather than polling without
  an adapter. Cleanup always closes callback and transport and releases the native
  lease, even after JVM cleanup fails. Reconnect preserves goals and JVM receipts.
- Preserved prior enabled state across bridge reconnects; a durable explicit Start token
  rearms only once. Reconnecting cannot erase a human Stop or replay an uncertain command.
- Lab reads observation timestamps/freshness separately from process liveness; stale
  combat data no longer renders as a current activity. Ten seconds is a display-health
  threshold, not a task execution deadline.
- Validation: 85 native/priority/preflight tests passed; all 119 Lab tests completed
  (one existing Windows symlink skip); real Java adapter contract passed for both API
  variants after the new exact artifact pin. Tests include actual callback-port release,
  failed JVM cleanup, stale data with live heartbeat, manual Stop and preserved queue.
  Independent final review found no blockers in the addon, evidence or bridge changes.
- Fresh process/port and exclusive-file checks confirmed the owner's game was closed.
  Backed up and replaced only auto-defense-0.1.0.jar; verified SHA-256:
  bdc144486e720b9a31a58544883593c7f0412e9a8da8262c6bf34f618d38f736.
  UnionClef and TreeGrow hashes are unchanged. Gradle/game evidence and backup:
  Codex-Development-Lab/audit/null-target-2026-11-12/.
- Gracefully retired the owned crashed game worker and restarted only Lab after no
  Telegram request was in flight. Restored prior settings and Telegram lifecycle;
  read-only healthz/check confirmed API v4, HTTP 200 and can_post=true. Game execution
  remains explicitly paused until the owner's Start. No test post was sent.
  Local runtime installation record: .saruman/audit/crash-recovery-2026-11-12/.
- Boundary: full restart/reconnect in the owner's Minecraft and broader combat scenarios
  remain live acceptance checks. The six game assertions cover this specific regression.

## 2026-11-12: Player priority and native idle activities

### Investigate
- Native mode bypassed the old random activity catalogue and could only queue trees;
  the owner's tree background switch was off. Chat and voice shared the player inbox,
  but voice did not signal pending input before response generation.
- Auto Defense used the same latch for death/world reset and manual Stop. The bridge
  also called UnionClef cancelUserTask for personal task cancellation: pinned bytecode
  confirms that this disables the global TaskRunner, creating another manual-stop latch.
- Verified exact installed APIs: Task.isFinished()/stopped() take no parameters;
  SingleTaskChain.setTask(null) stops its root/children without disabling TaskRunner.
  SQLite context-manager transactions did not close connections deterministically.

### Plan
- Keep native execution and existing model roles. After 20 seconds of actual idle,
  select tree growing or following a random alive loaded player, excluding self.
- Preempt personal activities for text/voice requests, preserve verified progress,
  distinguish reset recovery from explicit manual Stop, and release only owned roots.
- Recover from task/API/database failures without blind replay of uncertain mutations;
  publish actual activity/wait reasons to Lab. No additional retry pause after failure.

### Implement
- Added typed follow goals, random activity/player choice and an idle clock reset by
  actual work, dialogue, defense, disconnect and respawn. Failed hobbies prefer an
  available alternative; player requests bypass the idle timer. Follow departure,
  defense interruption, lost response and cancellation retain receipt identities.
- Text and STT voice signal player priority before model completion. The native prompt
  directs feasible requests to enabled tools. Legacy activity execution remains off.
- The shim distinguishes RESET/manual Stop, exposes alive/player/command facts and
  detaches only its exact finished/stopped root. Tree cancellation calls finish and
  scoped detachment, not the TreeGrow helper that disables the global runner. Native
  death ends the old attempt; after respawn new work can start without a Stop/Start loop.
- GoalStore enables WAL and closes each connection explicitly; database lease/history
  errors remain inside the dispatcher boundary and recover on a later tick.
- Lab shows actual work/wait reasons and waiting_respawn rather than a generic pause.
  Native panel documents the two available personal activities.
- Validation: 74 native/priority/preflight tests passed; real Java shim contract passed
  for both pinned API variants; all 117 Lab tests completed with one existing Windows
  symlink skip (191 successful tests in total, 1 skip). Actual Minecraft snapshot through
  the compiled shim confirmed alive/world/player/command fields without game actions.
  javac initially hit sandbox resource restrictions; permitted isolated execution passed.
  A Windows test exposed the SQLite handle leak, which was fixed before installation.
- Installed while the owner had stopped the character: gracefully retired the owned
  game worker, enabled background trees while preserving other settings, restarted
  only Lab and verified it remains stopped with the prior filter choice. No JAR/world
  replacement or Telegram publication. Next Start loads the updated game process.
  Local installation/probe/settings backup: .saruman/audit/native-idle-2026-11-12/ and
  .saruman/audit/native-idle-probe-2026-11-12.json.
- Boundary: following a second real player and live voice preemption still need an
  in-game check; the available live world contained only Saruman. These paths passed
  isolated protocol/event tests and the actual installed command ABI inspection.

## 2026-11-12: Preserve sapling drops while mining leaves

### Investigate
- The owner observed shears yielding leaf blocks during sapling acquisition.
  Independent pinned UnionClef bytecode review confirmed HAND is only a minimum
  tool gate; child tasks and the tool selector still prefer shears for leaves.
  A parent-only selection is overwritten before Minecraft processes input.

### Plan
- Keep navigation/mining in UnionClef. Add a scoped post-child hand check to the
  sapling task, excluding shears/Silk Touch while keeping Fortune. Preserve exact
  stack identity, cursor and inventory, and verify natural drops in an isolated game.

### Implement
- TreeGrow now selects an exact safe/empty hotbar slot or ordinary backpack SWAP
  after acquisition child ticks. It verifies the actual hand, clears held/queued
  attacks on failure and never discards equipment. Other tasks keep their tool policy.
- Gradle production remap and all 97 existing tests succeeded in the restricted
  offline runner. A natural-leaf fixture reproduced the original unsafe server
  break, then completed the patched cycle: 11 leaf breaks, 0 unsafe breaks,
  1 natural sapling drop, 0 leaf-block drops and a confirmed grown oak. Actual
  stack/enchantment checks and a full inventory/cursor SWAP passed in Fabric.
  Plain JUnit bootstrap lacked Minecraft access widening; those checks were moved
  into the real client rather than removed. Independent review approved evidence.
- Applied reviewed source under the paused TreeGrow project lock, with no canonical
  acceptance/status changes. Maintenance record:
  M-0000000000001285-c230905cc03ad428bfc3dc3d3ffcce4cb0d2aeb55e01dd4244e897681d35103f.
- Added the exact new hash to both Python and Java compatibility checks, keeping
  the previous verified hash. Seven native preflight/Java-contract checks passed.
- Owner authorized installation after closing Minecraft. Verified closed gateway,
  exclusive JAR read and original hash, backed up the old JAR and atomically replaced
  only .minecraft/mods/treegrow-0.1.0.jar. Verified new SHA-256:
  ed50500c43293f614c632b65f9c31c3a116a35c7759f16d547c392c3ae5708bf.
  The Windows filesystem rejected replacement while our own validation handle was
  open; reproduced on a disposable project file, then closed that handle before
  the atomic replace. No filesystem permissions were changed.
- Retired only the owned game Python worker to load new pins on the next Start;
  root Lab/Telegram remain active. No live game commands, item grants or replay of
  old tasks were issued during installation. Full evidence/backup/source/report:
  Codex-Development-Lab/audit/sapling-drops-2026-11-12/.
- Boundary: full natural-drop cycle tested with shears and Silk Touch together.
  A separate Silk-Touch-only full cycle and birch gameplay were not run. The actual
  owner's newly installed client still needs a retest after starting Minecraft.

## 2026-11-12: Rebind stale Minecraft handles and resume existing commands

### Investigate
- Private history showed an accepted two-tree workflow, a statement "already in
  the world" misread as connect, and another queued copy of the planting request.
- Fresh read-only probes confirmed Minecraft was in-world with working addon APIs.
  Redis mod_runtime identified the old worker's exact error: Py4J Target Object ID
  does not exist while calling the cached Java constructor. Lab showed a generic
  waiting_world instead of this adapter failure.

### Plan
- Rebind stale JVM handles for snapshot reads, never retry uncertain mutations.
  Expose adapter errors, pass pending workflow summaries to the private interpreter
  and add resume without another join or duplicated gameplay.

### Implement
- NativeMods recreates its loader/class/client/constructor handles on a failed
  snapshot and retries that read once. Shared JVM receipts remain in their existing
  registry. Mutation errors still propagate for receipt-based reconciliation.
- Native errors reach Lab status; an unavailable API no longer means "world closed".
  Added resume and current workflow context to admin interpretation. Failed/completed
  tasks cannot be silently reopened; a continuation retains step and publication IDs.
- Installed with the game itself open. The original native task was confirmed
  queued, 0/2, no run_id/world before pausing. Archived both original command plans
  and the interrupted attempt; linked the owner's duplicate clarification to the
  original workflow, retained its publication steps and resumed one two-tree attempt.
  Full local maintenance record: .saruman/audit/world-queue-recovery-2026-11-12.json.
- Live result: world/API recovered, a new native run started and reported failure
  "sapling not found within 64 blocks", 0/2. No success publication was queued.
  The UI now extracts exact failed-goal reasons from saved evidence. Native manual
  pause remains respected; no saplings were supplied and no native stop was bypassed.
- Validation: 114 Lab checks passed, one existing Windows skip; 53 native preflight/
  runtime checks passed. A live snapshot recovered from an intentionally stale local
  Java proxy. Isolated Chrome showed the exact fixture failure and retained scroll
  offsets over three auto-refreshes without JavaScript errors.

## 2026-11-12: Preserve scrolling in private control

### Investigate
- Three-second admin polling rebuilt the full page and reset scroll offsets in
  the command history and nested result panels.

### Plan
- Preserve the current page and control-panel offsets by stable command IDs
  during same-view renders. Reserve screenshot dimensions before image loading.

### Implement
- Restore vertical/horizontal panel offsets and page position after rendering;
  skip restoration on view changes. Screenshots expose their known dimensions.
- Validation: isolated Chrome with synthetic read-only APIs retained page/history/
  command-result/game-result positions at 180/320/118/85 px over three real auto
  refreshes, including delayed photo responses; no JavaScript errors. Static
  files are served directly, so the owner only needs to reload the browser page.

## 2026-11-12: Recover lifecycle after SQLite contention

### Investigate
- The two-tree command correctly submitted grow_trees(count=2). Native progress
  was 0/2 when the watchdog cancelled it after Lab stopped heartbeats. The Lab
  stderr identifies an uncaught sqlite3.OperationalError (database is locked)
  in lifecycle publish(). The UI continued reporting its cached running state.

### Plan
- Keep IPC independent of journal commits, retry failed iterations, reduce reader
  contention, expose liveness and show actual standalone command results.

### Implement
- Send game heartbeat before SQLite publication; a journal error no longer kills
  the lifecycle loop. Start can recover a dead thread; Stop reaches the game even
  if persistence fails. Added WAL to the local outbox and guarded dialogue/workflow
  retry loops against the same SQLite exception class.
- Snapshot reports thread liveness/freshness, with an explicit interrupted state
  instead of stale running cards. Standalone game commands now show their exact
  durable outcome, including legacy cancellations, without rewriting history.
- Validation: 111 Lab checks passed and one existing Windows symlink check skipped;
  72 Telegram checks passed. Regressions cover SQLite contention, heartbeat ordering,
  failed-iteration recovery, dead-thread Start, Stop under journal failure, WAL
  readers/writers, stale UI and command cancellation. JavaScript syntax passed.
- The cancelled 0/2 task is preserved, not replayed automatically.
- Installed after confirming the native agent had stopped and no Telegram send
  was in flight. Restored the owner's existing enabled runtime and filter choice.
  Live probes beyond the 15-second watchdog window show advancing IPC heartbeats,
  owner_connected=true, agent_running=true and a healthy Lab thread. WAL is active;
  the old task remains cancelled with no directive re-submitted.

## 2026-11-12: Durable private workflows and Minecraft photos for Telegram

### Investigate
- Admin interpretation allowed only one operation; compound world names swallowed
  the remaining command. Game acceptance was not a completed action. Telegram
  gateway API v3 and its durable outbox supported text only.

### Plan
- Reuse the admin and Telegram model roles. Persist ordered steps and stable IDs;
  advance only from the exact game result and confirmed Telegram delivery. Capture
  Minecraft's framebuffer into project storage and extend the existing gateway.

### Implement
- Added private workflow checkpoints, per-command game result journals, explicit
  cancellation/uncertain outcomes and ordered UI progress with photo previews.
  Completed steps and post identities survive restarts; Stop pauses orchestration.
  Native cancellation remains a cancellation, never an implicit replay or success.
- Public captions receive only selected confirmed game facts. Observed workflow
  tree events are reserved for that workflow's post, preventing duplicate scheduled
  coverage. The configured Telegram role prepares text; no new model role was added.
- Added Minecraft 1.21.11 screenshot adapter, validated project-owned PNG storage,
  gateway API v4 multipart sendPhoto, immutable text/photo identity and reconciliation.
  An old gateway holds photos for upgrade while existing text delivery still works.
- Added owner-run update_gateway.py with syntax checks, service health verification
  and file rollback; preserves credentials/database/channel and sends no test post.
  Administrative passwordless SSH was unavailable, so VPS installation is pending
  the owner's SSH password entry. See telegram-gateway/WORKFLOWS_RU.md.
- Live check captured and inspected a real Minecraft frame without publishing it.
- Validation: 104 Lab checks passed plus one existing Windows symlink skip;
  110 game-role/action/runtime checks and 72 gateway checks passed. JavaScript
  syntax passed. Installed the local update while the character was stopped;
  no gameplay command or Telegram publication was submitted during deployment.

Older completed work: [archive](archive/12-11-2026-lab-completed-history.md).
