# Progress

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
