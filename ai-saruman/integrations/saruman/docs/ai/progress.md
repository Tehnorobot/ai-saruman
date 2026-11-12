# Progress

## 2026-11-12: Truthful private publication commands and delivery status

### Investigate
- Live metadata showed the last channel post was confirmed sent 124 seconds before
  the owner's general publish command. Automatic posting was enabled. The admin
  model requested a draft ID, while the UI labelled that clarification accepted.
  Admin commands also lacked a current publication summary and delivery tracking.

### Plan
- Resolve an unambiguous existing draft without requiring the operator to copy its
  ID. Never create or resend a post implicitly. Separate informational answers,
  queue acceptance and confirmed delivery, preserving original command records.

### Implement
- Added atomic draft selection/queueing and command-linked audit records. Ambiguous
  candidates require selection; sent/unknown states never requeue automatically.
- Private history observes the linked outbox result, including duplicate request
  lookups. Existing detail-only answers display as answered without rewriting the
  original log. Added readable Russian UI statuses and concise result text.
- Models receive counts/last-sent metadata rather than full post/event history.
  Simple publication commands bypass the model. Publication queues remain subject
  to the existing Telegram enable and character Start controls.
- Validation: 71 Lab checks passed (one existing Windows symlink skip) and 71
  Telegram checks passed; an additional focused fake-gateway integration verifies
  repeated commands result in one send. JavaScript syntax checked offline in Node.
- With the character confirmed stopped, restarted only Lab. Live read-only checks
  verified the original clarification now displays as answered. Publication rows,
  attempt IDs, command IDs and Telegram settings were unchanged. The character
  remains stopped; no real post, LLM call or settings edit was performed.

## 2026-11-12: Named singleplayer command bypasses empty saved-server configuration

### Investigate
- Read-only live inspection reproduced the owner's case: the private admin model
  answered that test_3 needed a server address; no join was submitted. The saved
  connection target was empty. The named folder in the chat never reached the
  existing singleplayer loader.

### Plan
- Reuse connect for literal authenticated named-world commands and select world
  mode for that request. Preserve saved server settings and private/public boundaries.
  Validate before Start; do not activate the character on an invalid request.

### Implement
- Added direct handling of named local-world commands, including quoted names and
  Cyrillic. Models cannot invent another folder. Explicit local names work without
  preset connection settings; failures still ask for an exact saves folder.
- Empty names cannot fall back to a configured server. A refused concurrent join
  cannot retarget the in-flight probe. Existing request idempotency is preserved.
- Regressions reproduced the missing routing and premature Start. Full Lab suite:
  64 tests passed, one existing Windows symlink privilege skip. No real join,
  publication, LLM call or settings change was made by these checks.
- After the owner stopped Lab and approved installation, launched the updated Lab
  on 8765 with UTF-8 output. Read-only live runtime/admin checks passed; the
  character remains stopped and command history is retained. The actual game
  entry will occur only when the owner repeats the command.

## 2026-11-12: Exact saved-world selection and actionable name correction

### Investigate
- Existing-world lookup already used the running client's saves directory, but a
  missing folder became a generic filesystem exception. Regression tests also
  reproduced a separate RPC bug: the request marker overwrote the cached Java
  classloader, so later polling could fail or reject a successful world entry.

### Plan
- Preserve exact folder selection and the current profile boundary. Ask the owner
  for the exact folder name when no valid save matches; never choose a similar
  world. Keep request intent and classloader caching separate.

### Implement
- Added structured missing/invalid-name results and a visible needs_world_name
  state, with a Settings correction instruction. Rejected requests stop polling
  until the owner corrects the target and explicitly retries.
- Fixed the request-marker/classloader collision and cleared refused request
  intent. Updated UI labels and the autonomy guide.
- Validation: 57 Lab tests passed, one existing Windows symlink skip. Actual
  Java compilation/execution checked exact and Cyrillic save names, missing saves,
  missing level.dat, traversal rejection and duplicate-request handling. Python
  regressions checked refusal without guessing/retrying and join confirmation.
- No real game launch/world entry, LLM call, publication or settings edit. Lab
  was not running on port 8765; the updated code applies on its next launch.

## 2026-11-11: Unified Lab autonomy and remaining approved steps

Implemented one Start/Stop, independent scheduled Telegram, scoped DM/comment
replies, private administrative commands and configured Minecraft connection.
Reused durable queues/roles and separate environments; preserved native safety
and the user's unrelated changes. Gateway API v3 update and physical game/Telegram
checks remain owner-operated. Updated local Lab is running on 8765 with the
character stopped; 228 relevant checks passed, one Windows skip, browser smoke
and read-only live endpoints passed. [Implementation and validation](archive/11-11-2026-unified-autonomy.md).
Full use/test guide: [Autonomy](../../../../docs/AUTONOMY_RU.md).

## 2026-11-11: TreeGrow falsely cancelled after defense handover

Confirmed ordinary UnionClef task completion was mistaken for manual Stop because
combat retained a borrowed-runner claim. Fixed both normal/rescue handovers and
preserved actual Stop/safety. Two regressions reproduced the issue; all 61 Gradle
checks/remapJar and 51 Python runtime/preflight checks passed, plus the Java shim
contract. Paused Lab history preserved. After the owner closed Minecraft,
installed the new JAR with a verified backup; real planting/resumption retest
remains pending.
[Investigation, implementation and evidence](archive/11-11-2026-treegrow-false-stop.md).

## 2026-11-11: Auto Defense target-free handover

Diagnosed unsafe-target cycling and priority-60 grace rearming in the installed
addon. Fixed source, passed 52 isolated Gradle tests/remapJar, retained native
safety/handover settings, and updated Saruman artifact compatibility. After the
owner closed Minecraft, installed the new JAR with an old-JAR backup and verified
hashes. Paused Lab source/memory updated without changing acceptance history.
Real gameplay handover retest remains pending.
[Investigation, implementation and evidence](archive/11-11-2026-target-free-handover.md).

## 2026-11-11: Startup readiness and installed Auto Defense addon API

Fixed cold-filter startup grace and compatibility with the installed Auto Defense
0.1.0 API. Preserved legacy support, native manual Stop, exact artifact checks and
single supply ownership. 53 Python checks and the real Java harness passed;
live read-only snapshot and full Doctor --live passed in the owner's open world.
No game actions, LLM generation, mod installation or service restart performed.
[Investigation, plan, implementation and validation](archive/11-11-2026-startup-readiness-addon-api.md).

## 2026-11-11: Per-role providers/models in Saruman Lab

Implemented shared Lab/game model settings for chat, persona, actions, Telegram
and optional legacy/helper LLM calls. Added MiniMax M Plan Explore integration,
native reasoning, shared accounting and independent Telegram selection. Preserved
existing profiles and guarded running requests/stale edits. 183 relevant checks
passed; one Windows symlink skip. A broader legacy suite has one pre-existing
message-text assertion failure. Live MiniMax access awaits the owner's key.
[Investigation, plan, implementation and validation](archive/11-11-2026-per-role-models.md).

## 2026-11-11: Preserve LF when provisioning the Telegram SSH tunnel from Windows

### Investigate
- The owner reached the VPS but sh failed on the first `set -eu` line. Python
  text-mode subprocess stdin translates LF to CRLF on Windows; this is independent
  of the source file encoding and Git checkout settings.

### Plan
- Send the remote script as UTF-8 bytes; preserve the existing key/config on retry.

### Implement
- Extracted install_public_key and removed text-mode stdin conversion. Failed SSH
  now reports its exit code without a Python traceback; client config stays unchanged.
- Added regression tests exercising real child-process stdin on Windows and the
  existing-key/config-preservation paths. Tests do not contact SSH or read real keys.
- Validation: reproduced old CRLF versus fixed LF at a real Windows child-process
  stdin; all 52 gateway/automation/provisioning tests passed. Owner rerun on VPS pending.

## 2026-11-11: Autonomous Telegram integration

Implemented game facts → existing persona model → durable outbox → VPS gateway,
with review/autopublish controls, history, inbox and SSH recovery in Character Lab.
165 tests passed; 1 Windows symlink privilege skip. Live installation awaits owner
SSH authentication. [Investigation, plan and implementation](archive/11-11-2026-telegram-autonomy.md).

Previous work: [mod implementation guides and document organization](archive/24-10-2026-before-mod-runtime-review.md).

## 2026-11-07: Simplify Mage Home requirements after the Lab pipeline review

### Investigate
- User approved the specification audit. Found irreversible lifetime build/supply limits, an ambiguous completed-stage check, an undelivered adjacent dependency JAR, overly prescriptive site scanning, and unclear empty-site reservation/palette handling. Compared the requirements with current Lab input handling and prior progress/recovery failures.

### Plan
- Preserve the exact house, palette cycle, 100-block rule, durable resume and exclusive UnionClef ownership. Replace internal algorithm prescriptions with observable site requirements and recoverable operation attempts; separate current status from the house registry.

### Implement
- Updated mage-home/MAGE_HOME_SPEC.md: removed lifetime limits and fixed placement order, simplified support/site checks, defined same-record candidate replacement and palette assignment, made resume revalidate the world and start a new attempt, and excluded ready stages from no-support failures. Replaced periodic damage monitoring with explicit status/resume checks, added small JSON examples, and made the proposed implementation order advisory. Command/API starts respect foreign task ownership.
- Updated mage-home/README.md with the actual Markdown-only Lab upload boundary and paused-project JAR staging into workspace/libs; no extra host folder mounts. Confirmed the input mod ID is altoclef.
- Validation passed: full blueprint expansion (180 occupied/65 air/178 items), furniture support and central passage, dependency SHA-256/metadata, four JSON examples, UTF-8, local links, removed obsolete limits and whitespace. Independent read-only reviews checked recovery and pipeline compatibility. These checks validate the documents, not physical construction. No generated mod, running project, game, provider, container or Git commit/push was changed.

## 2026-11-07: Specify a deterministic client-side home-building addon

### Investigate
- User requests a concise but unambiguous specification for MAS implementation: fixed-size wizard homes, persisted coordinates/state, bounded site scanning and a new variant outside 100 blocks. Read local runtime constraints, inspected the installed UnionClef artifact/hash and Task/placement/navigation ABI read-only, plus the existing addon's working dependency pins and public Fabric/UnionClef references.

### Plan
- One self-contained requirements file with a fixed coordinate blueprint, three palettes, explicit scan/distance/placement/supply rules and a persistent realm-scoped registry. Supply the exact UnionClef input artifact so the builder need not infer an incompatible upstream API. Do not require private persona/defense projects for core acceptance.

### Implement
- Added docs/mod-development/mage-home/MAGE_HOME_SPEC.md and README.md; linked from the docs index. Defined physical client-side construction, 180 occupied/65 air cells, one-item bed/door placement, bounded inventory supply, partial-state resume and actual-world verification. Copied the exact read-only installed UnionClef JAR into dependency/ as local build input (not the new addon; ignored binary, not staged).
- Validation passed: expanded coordinate blueprint/material counts (178 items), unobstructed central passage/furniture support, 100/101 radius boundaries and return to prior house, UTF-8/Markdown/relative links, dependency SHA-256 and ABI entries, diff whitespace. These validate the specification, not actual construction. No implementation, game controls, model calls, live DB writes, restarts or Git commit/push.

## 2026-11-06: Use saruman_mag consistently as the Minecraft nickname

### Investigate
- User requests the underscore nickname everywhere. Found 31 nickname references across the character configuration/aliases, HybridAgent self-message guard, experiment prompts, equipment/command fixtures, Windows instructions and an archived development note. Ignored local env/config sources were included in the search without printing secrets.

### Plan
- Update all project-controlled references while preserving existing unrelated changes and file encodings. Verify the profile, self-message guard, command parsing and equipment tests; do not rewrite historical databases/logs or change the Minecraft account.

### Implement
- Replaced the nickname in ten files, including tests and the archived note. Validation: 145 relevant profile/role/command/equipment/scepter tests passed; undefined-name and diff whitespace checks passed. Full source/config/document scan including ignored local configuration found no remaining old nickname outside generated data/caches/logs. No live game controls, database edits, provider calls, restarts or Git commit/push. Full Python runtime restart loads the new nickname; actual Minecraft login must use the same name.

## 2026-11-06: Add an operator game-memory reset for a fresh initialization

### Investigate
- User requests a console action to erase information accumulated during play. Learned users/dialogs/relations and mood are in PostgreSQL; native goals and role intents are in separate SQLite stores; Redis restores tree checkpoints across normal restarts. Ordinary Stop does not stop all collectors, so deleting live rows alone can repopulate them or retain in-memory dialogue.

### Plan
- Schedule an explicit reset from Control/tool console or CLI, apply at the next full runtime startup before checkpoint restoration and collectors, and block Start until applied/cancelled. Preserve authored personality/stories/voice, model profiles, budget/usage history, credentials and activity preferences. Fail startup on partial cleanup, retaining an idempotent reset request.

### Implement
- Added memory_reset.py, a Gradio panel, local !reset_game_memory / !cancel_memory_reset commands and launcher reset-memory [--cancel]. Startup clears learned relational rows in one transaction, intents, goals/meta and only saruman-prefixed Redis keys. Normal restarts keep their existing behavior when no reset is requested. Published runtime PID and active-request checks protect against another live runtime.
- Cancellation is available before cleanup starts; a partially applied request must finish on retry. Added isolated real SQLAlchemy/SQLite fixtures, mocked Redis, foreign-key/rollback checks, preservation of authored disabled stories/profiles/accounting, startup ordering, UI/console/CLI admission and no checkpoint resurrection. User documentation added.
- Validation: 180 relevant regression tests passed; final 11 reset tests passed after adding UI status refresh. Ruff, undefined-name checks, AST, UTF-8, documentation links and diff whitespace passed. PostgreSQL behavior is covered with the shared SQLAlchemy models and foreign-key-enabled isolated SQLite, not a live destructive PostgreSQL run. Confirmed no live reset marker exists. No live memory erasure, provider calls, gameplay, process/container restarts or Git commit/push; full restart loads the new controls, and a separately requested subsequent startup applies reset.

## 2026-11-03: Replace oversized server ritual design with a client-only minimum

### Investigate
- User explicitly requires client-only and rejects the size of the server specification. Clarified that other players need no installation when the client sends permitted vanilla particle/playsound commands; without permissions a client cannot broadcast its own local particles. Checked version-specific Fabric client-command documentation.

### Plan
- Replace the server design rather than layering a client shim onto it. Four one-shot particle clouds with at most one sound, a small public Java API, local commands and a five-second cooldown; remove server installation, animated command rings, request history and chat protocol.

### Implement
- Rewrote RITUALS_SPEC.md, README and the optional Saruman checklist; updated docs index. Defined queued versus sent, command availability checks, bounded pending work, world/death invalidation, ordinary-client observation and test logs outside source snapshots. No private integration is required for mod acceptance.
- This supersedes the previous server specification; it does not modify the Lab's existing generated server project, running agents or installed JARs. Final spec is 105 lines instead of 235; UTF-8, Markdown, links, JSON example, four presets, client metadata, removal of server/private contracts and diff whitespace checks passed. No provider calls, gameplay, DB changes or Git commit/push.

## 2026-11-02: Make ritual acceptance independent of private integration context

### Investigate
- User correctly identified that the mod-building system cannot test Auto Defense/TreeGrow without their artifacts or private runtime. The spec also mentioned the existing bridge/ActionAgent, trusted-message parsing, second-client observations and concurrent runs without clearly assigning the test environment/owner.

### Plan
- Make the single implementation spec self-contained, separate core automatic tests from actual Minecraft observations, and move Saruman-only checks to an optional owner checklist.

### Implement
- Replaced named third-party combat/tree acceptance with ordinary manual movement/use/attack tests and a source-level no-control-writes check. Specified clean Fabric/API/mod test setup, two-client visual requirements, nine-UUID unit fixtures rather than nine real accounts, and NOT_TESTED when a real game/client is unavailable.
- Replaced private runtime implementation requirements with a generic command boundary. Added SARUMAN_INTEGRATION_CHECKLIST.md for actual JAR/bridge dependencies, message authenticity/TTS suppression, tree-result separation and graceful absence; README distinguishes the developer input from optional owner checks. Validation passed: absence of private mod/API references in the standalone spec, JSON, relative links, UTF-8, Markdown fences, acceptance count and whitespace. No code/runtime/JAR changes, model calls or Git commit/push.

## 2026-11-02: Specify a small server-side cosmetic ritual mod

### Investigate
- User requests a concise implementation specification and confirms the staff mod was removed. Read project rules/progress and native integration requirements; checked official Fabric 1.21.11 tools/commands documentation and Mojang release notes.

### Plan
- Use a standalone server-side Fabric mod with vanilla particles/sounds, four bounded presets and a command/JSON contract. No client installation, movement ownership, staff dependency, model calls or changes to current gameplay.

### Implement
- Added docs/mod-development/rituals/RITUALS_SPEC.md and README.md; linked the folder from docs/README.md. Spec defines recipes, geometry/timing, actor-scoped permissions, run/request identity, duplicate/Stop behavior, load limits, lifecycle and concrete acceptance cases.
- Clarified that server installation is required for multiplayer visibility, cosmetic completion is not tree growth or proof of client rendering, and particles already sent survive Stop until their natural expiry. Reviewed schema/lifecycle/deduplication for contradictions; UTF-8, Markdown fences, relative links, JSON/UUID example, preset/acceptance counts and diff whitespace checks passed. This is a specification, not a built/installed mod or Python integration. No runtime/DB/env/JAR changes, provider calls or Git commit/push.

## 2026-11-02: Assess musical performance addon, notation formats and MiniMax implementation

### Investigate
- User requested feasibility research, existing client mods, score storage/conversion, limitations and difficulty for MiniMax coding models. Read project runtime docs and the current Lab model catalogue (MiniMax-M3.1-Flash).
- Browsed primary publisher/repository pages: ProjectMichi NoteBot with a Fabric 1.21.11 beta release; separate GPL Nat1an Notebot; SongPlayer and its visible release compatibility; henkelmax AudioPlayer and a confirmed server-side Fabric 1.21.11 build; official NBS format, Java MIDI API, Mojang note-block description and official MiniMax model page.
- Distinguished physical server-visible note-block interactions from client-only preview and SVC audio playback. NBS v6's added trumpet instruments target 26.1, so parser acceptance does not imply native support on 1.21.11. No installed JARs or game state changed.

### Plan
- Recommend an optional client music addon / adapter, first using a prepared scene and deterministic local playback. Reuse existing software after API/ownership verification rather than repeating fragile private-field integration. Keep audio delivery as a separate backend/alternative.

### Implement
- Added docs/mod-development/music/README.md and FEASIBILITY.md with sources, exact compatibility confidence, score pipeline, proposed JSON example, timing/pitch conversion, rendering losses, physical stage constraints, control arbitration and phased acceptance criteria. Linked from docs/README.md.
- Documented one goal per composition, whole-intent Stop, no auto-resurrection after cancellation, optional music capability, no LLM between notes, no implicit creative/teleport, and failure isolation from follow/trees/defense.
- This is research/design, not a mod implementation. No installs/builds/provider calls/music playback/DB changes/runtime restarts or Git commit/push. Documentation checked for UTF-8, fences, relative links and JSON example validity. Complexity ratings are engineering estimates, not benchmark claims or delivery guarantees.

## 2026-10-28: Keep uncertainty checks private instead of repeated spoken disclaimers

### Investigate
- User reports repeated claims of inability and «не буду выдавать действие за действительное». PersonaAgent receives many private verification constraints and action notes, including queue acceptance versus outcome, which can leak into its public wording.
- Read-only latest intents show actual queued work, unknown outcomes and completed conversation are distinct; lack of observation must not become a general claim of inability.

### Plan
- Preserve grounding and honest partial progress while explicitly distinguishing unknown, pending, failed and unsupported. Treat honesty policy as private reasoning, not spoken personality.

### Implement
- Updated PersonaAgent boundary and shared character profile: no recitation of honesty/verification policy; name one observed difficulty or admit uncertainty briefly, don't invent obstacles, don't append caveats to normal conversation/success and don't repeat an unchanged failure after each new message. Running work is not described as impossible or abandoned.
- Updated character documentation. No game execution/state or model selection changed; no output text replacement, extra model calls or live generation. 60 focused role/speech/character tests, Ruff, YAML shared-core and whitespace checks passed. Full runtime restart is required to reload the cached prompt; actual generated style remains to be observed.

## 2026-10-28: Make stop-growing requests effective rather than generic acknowledgements

### Investigate
- User reports another monotonous response after asking not to grow trees. Read-only intent history showed «Прекрати строить деревья» passed the native run ID b8bd…:0 to cancel_mod_goal, while GoalStore.cancel expects the durable goal ID. The tool unconditionally returned cancel_requested despite matching no goal.
- Background tree selection remained enabled. The original planner goal was eventually cancelled by player priority, but a subsequent tree goal completed and another started. Rewording persona alone would conceal ineffective cancellation.

### Plan
- Validate cancellation identity and return accurate results; provide one bounded tree-stop tool that also disables future autonomous tree scheduling using the existing UI settings.

### Implement
- cancel_mod_goal now resolves an actual active goal ID or exact run ID and rejects unknown IDs; returns matched IDs. Added stop_tree_growing to cancel active tree goals and persist trees weight=0, preserving other weights/commands. Guarded by current player/operator context and the normal ActionAgent tool allowlist.
- Updated ActionAgent instructions to use this tool for general tree-stop requests. Returned verified effect metadata reaches PersonaAgent so it can distinguish background disabling from completed native cancellation. Explicit later grow_trees requests remain possible; Gradio can restore background selection.
- Added real goal-store regression tests for invalid/run-ID cancellation, stopping all tree goals and preserving an unrelated follow command. 114 focused tests and the final 505-test relevant regression suite passed; Ruff/undefined-name checks passed.
- Attempted guarded application of the earlier in-game stop request only if it was still the latest intent; it had changed, so no live goals/settings were altered. No live model calls, process restart or Git commit/push. A full runtime restart loads the tool and speech changes; repeat the desired current stop request afterward.

## 2026-10-28: Give PersonaAgent conversational initiative and topical personal memories

### Investigate
- User reports sparse, assistant-like speech and lack of personal recollections despite an existing character profile. The persona boundary focused on execution restrictions, ActionAgent could supply scripted conversational answers and lore was a static 24-scene prompt appendix. There was no independent conversational initiative.
- Existing 48 authored scenes cover trees/books/travel/teaching/politics/craft/provisions/solitude. Preserve edited or disabled PostgreSQL entries and distinguish authored past from server observations and Tolkien canon.

### Plan
- Keep game authority unchanged while giving the voice clear positive examples, opinions, interest, dry humour and concrete recollections. Select a small topical memory context locally without a second model call.
- Add bounded speech-only initiative outside combat/player work; keep operator disable control, model limits and stale-publication guards.

### Implement
- Revised shared personality and PersonaAgent boundary: normally 2–4 connected sentences for substantial conversation, short acknowledgements when appropriate, curiosity/vanity/self-irony, no customer-support offers or recitation of private execution notes. ActionAgent no longer scripts ordinary public answers; its game decisions remain authoritative.
- Added PersonaContext: up to two topical enabled authored scenes per reply, rotate recently supplied scenes, retain an explicitly empty/disabled DB bank. No generated memory becomes factual game history; no extra inference for retrieval.
- Added speech-only remarks after 3–5 quiet minutes using PersonaAgent's own provider/budget. Suppress while dead/disconnected/in combat, during player work, queued/sounding speech or role failure. Ambient needs_action cannot escalate to ActionAgent. A new player message or combat discards an in-flight ambient reply. Disable via SARUMAN_PERSONA_INITIATIVE_ENABLED=false; env examples and role/character docs updated.
- Validation: final full relevant suite passed 503 Python tests; Ruff, AST, Markdown links and YAML single-core alias checks passed. Coverage includes actual-bank topic parity, topic rotation, disabled memories, no additional retrieval inference, bounded initiative, operator disable, player preemption and no ambient game goals. An intermediate focused run hit a Python import-lock KeyError in Gradio's IPython detection; the affected UI test passed in isolation and the final full suite passed without recurrence.
- A proposed single live Codex style-preview request was rejected by automatic approval review as a real provider call under an earlier live/paid-call restriction. No preview request executed; no workaround/retry. Actual prose quality remains NOT_TESTED. No running-agent restart, game controls, key/model changes, PostgreSQL migrations or Git commit/push. A full local runtime restart loads the new personality/context/initiative.

## 2026-10-28: Clear current queued requests and cancel obsolete follow intent

### Investigate
- User requested clearing accumulated tasks and proposed a future one-minute request lifetime. A prior follow intent was producing replacement goals after individual cancellations; clearing only goal rows would allow replay.

### Plan
- Use the existing supervisor Stop lifecycle to invalidate workers and cancel complete in-memory/persisted intents together with goals. Resume the previously enabled agent afterward. Do not delete history or implement an unspecified timeout for running tasks.

### Implement
- Backed up the one then-active goal and enabled state to .cache/queue-clear-backup-1793225619436846300.json. Called the local Gradio Stop endpoint, verified agent_enabled=false, native_busy=false, no active goals and no open saved intents. Called Start to restore the previous enabled state.
- Historical goals remain recorded as cancelled rather than deleted. The active TreeGrow was cancelled as requested while clearing all tasks. No mod removal, SQL bypass, source change, process/container restart or Git commit/push. One-minute request expiry is not implemented by this queue-clear operation.

## 2026-10-28: Release stale previous-world cancellation blocking the queue

### Investigate
- User explicitly authorized removal of the identified queue blocker, not removal of Auto Defense. Read-only state confirmed an old follow command a2381da9c32c47688ce8f57fb36031b2 in cancel_requested for a previous world.
- Reconcile tried to convert it to uncertain on world mismatch, but GoalStore correctly preserves cancellation intent and rejects that transition. The retained active cancellation then blocked every queued goal.

### Plan
- Finalize previous-world cancellation as cancelled without issuing stop/cancel commands to the current world. Preserve the store's cancellation-wins rule, existing progress and normal uncertain handling for other old-world goals.

### Implement
- Changed world-mismatch reconciliation to acknowledge cancel_requested as cancelled. Added regression tests for a following player command starting in the same tick and stale cancellation completing even during defense, with no foreign-world native controls.
- Saved the exact stale record to .cache/cancelled-old-world-backup-1793224923626402400.json, then finalized only that previous-world cancellation through GoalStore.update. No deletion of historical records or other queued tasks; no mod removal or process restart.
- Validation: 41 native-runtime tests passed, Ruff passed. Read-only live verification after finalization showed runtime running, native_busy=true and a TreeGrow goal running: queue processing resumed. This does not assert that a tree has grown or the player was reached.
- The record repair applies immediately; the source fix requires the next full Python runtime startup to prevent recurrence. No Git commit/push.

## 2026-10-28: Align TreeGrow adapter with the installed updated 0.1.0 artifact

### Investigate
- User authorized addon compatibility work after read-only diagnosis found a new installed TreeGrow JAR rejected by the pinned adapter. Verified ZIP integrity, metadata, actual bytecode, available Lab source and public read-only state. The version remains 0.1.0, but SHA-256 changed from 90c1a826… to edd7a699…; task implementation now exposes outcome/interruption and richer counters.
- The latest standalone task had ended during sapling acquisition, without any site/plant/bone-meal attempts, after scheduler interruption. The diagnostic scheduler snapshot included Unstuck priority 55 above UserTaskChain 50; this does not prove the interrupting chain's identity.

### Plan
- Pin the verified new artifact, publish its status fields, reset all terminal metadata on permitted same-object recovery and preserve native time/resource budgets. Do not treat any failure after a fight as a resumable cancellation.
- Keep scope in the Saruman compatibility layer; do not modify/rebuild the user's addon, disable competing chains or launch gameplay.

### Implement
- Updated both TreeGrow artifact checks in ModGateway to edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9. Snapshots now include outcome/reason/interruption/sites/uses/giveSends/remainingNanos.
- Resume requires the same owned run, a cancelled scheduler interruption with the precise native prefix, no success and no expiry. Clear terminal/success/result plus new outcome/interruption fields and republish native status; preserve started/site/search/budget/give counters and original task object. Reject manual cancellation/expiry/acquisition failure before releasing a retained root. Duplicate receipts retain idempotency.
- Python dispatch uses the same typed terminal metadata after observed defense, preserving real acquisition failure reasons rather than replacing them with resume_unsupported. Generic scheduler interruptions without observed defense remain terminal; this change does not silently override Unstuck or guarantee tree growth.
- Extended Java fixture coverage for new status/counters, same-object resume, duplicate delivery, manual/expired/failed rejection and existing command ownership/world/death guards. Updated native-runtime implementation compatibility documentation.
- Validation: Java 21 compilation and GatewayContractTest PASS; 492 relevant Python tests passed. Instantiated the production adapter constructor against the actual running Minecraft JVM: all three installed artifact checks PASS. Did not invoke supplier.get(), start/resume/cancel/defense or any game-control operation during live compatibility validation. Local compilation cache only; no installed JAR/source changes in Lab, live LLM calls, agent/container restarts or Git commit/push.
- A full local Python runtime restart loads the updated adapter scope; no Minecraft restart or Docker rebuild is required for these adapter changes. Actual in-game planting/resume remains NOT_TESTED.

## 2026-10-24: Diagnose silent persona and slow provider replies

### Investigate
- Read-only live Redis inspection confirmed an enabled hybrid runtime and empty speech queue. Request history showed the earlier configuration failure followed by PersonaAgent blocking; fresh runtime requests subsequently reached LiteLLM but timed out after 180 seconds. Codex action decisions still complete in approximately seven seconds.
- Authorized diagnostic: LiteLLM GET /models returned HTTP 200; one isolated short PersonaAgent generation with the selected Gemma failed with ReadTimeout after 180 seconds. An initial sandbox-restricted attempt failed before transport; no game actions or chat publication. Diagnostics have separate session IDs in usage history; unknown billed outcomes remain unknown.
- Found a second problem: valid speech was discarded after 45 seconds despite a configured 180-second model timeout. Speculative acknowledgement after four seconds could occupy the sole persona worker and delay the real response behind an obsolete request.

### Plan
- Align speech expiry with configured provider timeout, retain generation/phase/world guards, eliminate speculative acknowledgement calls and preserve safe provider diagnostics through the worker boundary. Do not silently change the user's selected model/provider.

### Implement
- Speech jobs now expire after the configured timeout plus 30 seconds, with earlier cancellation on Stop/new phase/world change. Removed speculative pre-decision persona calls. Added a sanitized RoleRequestError for actionable operator errors without leaking arbitrary provider text.
- Full regression suite passed 490 tests before the acknowledgement removal; final focused suite passed 42 tests, including slow-but-current output, deadline expiry and no speculative model work. Saved model settings and credentials were not changed; running process was not restarted. Server-side generation availability remains an external limitation, not a TTS success claim.
- A second explicitly bounded diagnostic (256 output tokens, 30 seconds) also timed out. This rules out merely waiting for the UI's large output allowance as a sufficient workaround; model backend/tool-call handling still needs investigation at the configured LiteLLM service. No additional generation retries were started.

## 2026-10-24: Diagnose LiteLLM connection failure and validate provider setup

### Investigate
- User reported a role request ValueError after selecting Gemma. Read-only inspection confirmed the saved PersonaAgent LiteLLM profile and missing LITELLM_BASE_URL/LITELLM_API_KEY in .env.saruman; the local shell has neither. The preflight raises before HTTP. Previous generic exception reporting hid which setting was absent.
- Read-only GET of the running Gradio /config confirmed the two role editors under Control on port 22228. No callback or model was invoked on the running server.

### Plan
- Expose fixed safe configuration diagnostics, reject saving incomplete API profiles, warn about already saved profiles and preserve chosen models/credentials.

### Implement
- Added ProviderConfigurationError with fixed variable names and restart guidance; role request history records the actionable message without URLs/credentials/provider response bodies. UI validates before saving and shows readiness warnings without network calls.
- Added three regression tests for incomplete-profile saves, existing-profile warnings and pre-HTTP configuration failures with no secret disclosure. Full relevant regression suite: 488 tests passed. Ruff checks passed. Provider responses mocked; live provider availability remains unverified because configuration is absent.
- No API keys copied, model profiles changed, live LLM calls, game actions, process restarts or Git commit/push. User must configure their endpoint/key or explicitly select Codex; runtime restart loads env and code changes.
- During final verification .env.saruman changed externally: both LiteLLM variables are now present, and offline URL syntax validation passes. The earlier running process does not automatically reload environment. Credentials themselves and live model responses remain untested.

## 2026-10-24: Separate action decisions from public persona and model settings

### Investigate
- User authorized the complete two-role implementation and supplied the Lab model editor/catalogue as reference. Inspected its provider/model/reasoning validation, local character prompt, native goals, tool admission and Gradio lifecycle.
- Native ChatAgent previously combined decisions, commands and public narration in one model context. A fun-tools import also eagerly created an unused legacy model client.

### Plan
- Preserve the exact player request for ActionAgent; isolate PersonaAgent from tools and factual memory. Keep separate asynchronous workers and native execution independent of speech.
- Persist validated per-role profiles and request usage; expose provider/model/reasoning and limits through native Gradio controls without API keys or live catalogue queries. Retain subscription defaults until explicitly changed.

### Implement
- Added HybridAgent with independent PersonaAgent/ActionAgent workers, conservative routing, original-request escalation, guarded schema-validated tool proposals, one mutation per round and durable intent/goal correlation. Native mode no longer creates legacy planner/core/chat model loops; legacy nickname model initialization is lazy.
- PersonaAgent generates chat and voice together, uses the character/backstory bank, has no executor and cannot turn its text into commands or factual memory. Stale generations, phases and world-session replies are discarded; slow speech does not delay action decisions.
- Counted tree goals run entirely through mods. Simple completed tree requests need no additional strong-model call; compound requests return to ActionAgent with their original text and confirmed observations. Covered goals that finish before the worker result arrives; failed tree goals cannot be restarted under a changed count to evade limits. Uncertain mutations are not blindly replayed.
- Added separate Codex/LiteLLM/DSLab clients, validated catalogues copied from Lab, independent reasoning/timeout/output/request/token settings, transactional SQLite reservations, actual known/unknown usage and safe error reporting. No implicit paid fallback or retries. Codex output setting is explicitly a budget reservation, not a hard generation cap.
- Added Control → «Агенты и модели»: two native Gradio editors with dependent provider/model/reasoning lists, save/load, request history and usage. Changes require Stop and completion of in-flight requests; secrets stay in environment. Updated env examples, doctor checks, direct httpx dependency/lock metadata and docs/dev/ROLE_AGENTS.md.
- Validation: 485 relevant Python tests passed, including 36 new role/provider/Gradio/concurrency/goal-recovery tests. New-module Ruff and undefined-name checks passed. Existing lock consistency was checked offline. Provider calls were mocked; SQLite fixtures were isolated. Existing unrelated ProfileTests mismatches described below remain outside this change.
- No live model requests, gameplay, key changes, process/container restarts or Git commit/push. First application requires a full local-runtime restart; Docker rebuild is unnecessary. Actual provider availability, model behavior and in-game integration still require live acceptance.

## 2026-10-24: Assessment of supplied native mods and character architecture

### Investigate
- User proposes replacing LLM-driven tree/combat execution with supplied Auto Defense 2 and TreeGrow and asks to study, assess and redesign the character logic.
- Read both supplied specifications, current bridge/controllers, ChatAgent background scheduling, native task sources and installed JAR metadata/public interfaces. The new requirements differ from the historical project specification: no attacks on players, swimming allowed, 6/10 HP recovery, read-only equipment needs, one generic oak/birch per tree run, three sites with 16 uses and five-minute limit.
- Found the relevant Lab sources in projects/0c95af0c97dd47/workspace (Auto Defense 1.0.0) and projects/66b5a30bdb6945/workspace (TreeGrow). Other defense projects are earlier/incompatible iterations.
- Installed artifacts inspected read-only: auto-defense-1.0.0.jar, treegrow-0.1.0.jar and exact UnionClef 0.95.61. Extracted selected classes into ignored .cache/native-mod-audit for javap; hashes recorded in the review. No live Minecraft control methods or models invoked.
- Confirmed integration gaps: defense public API only reads/publishes status, enabling uses internal toggle; no world/run sequence in current snapshots. Defense cancels UnionClef user tasks; TreeGrow onStop marks the task terminal, while defense command replay only captures @chat commands.
- Confirmed the installed TreeGrow abandonSite path does not ensure acquisition of a consumed sapling after first-site failure. Source and installed bytecode inspected; no gameplay reproduction claimed. Strict species/plant-only/resource limits are not exposed by current start().
- Existing autonomous procedures already have deterministic logic, but Autonomy runs inside ChatAgent._background_step, so continuation is coupled to the model's control loop. Legacy bridge combat/water/navigation writers would conflict with the native mods if retained unconditionally.

### Plan
- Evaluate and document the target architecture before claiming a complete runtime rewrite against incomplete control contracts: independent AutomationRuntime, persistent count-aware goals, thin native adapters, one movement/supply owner, goal-level tools and event-driven speech/LLM.
- Record required native API/ownership/resume changes and supported versus unsupported goals; retain existing runtime until a verified handoff is implemented. No blanket claim that installed artifacts are fully accepted.

### Implement
- [x] Added docs/mod-development/native-runtime/README.md, ARCHITECTURE_REVIEW_2026_10_24.md and MOD_API_REQUIREMENTS.md with verified facts, latest behavioral differences, target components, file-level concerns, count-aware scheduling, supply/potion semantics, interruption handling and staged transition.
- [x] Linked the review from docs/README.md and marked the old Auto Defense design README as historical relative to the supplied current specs/API.
- [x] Validated Markdown fences, JSON example, UTF-8 and relative links; whitespace checks passed. Documentation/audit only: the complete runtime rewrite has NOT been implemented or represented as complete. No game commands, builds/installs, tests that execute gameplay, database/Redis writes, Git commit/push or process restart.

## 2026-10-24: Hybrid routing clarification

### Investigate
- User explicitly retains LLM command execution for player requests outside the two mods. Player requests are priority 1, situational native behavior priority 2, and the deterministic mod-only scheduler priority 3.
- Previous review was too broad in hiding goto/command tools and did not state the mod-only restriction on the background scheduler clearly enough.

### Plan
- Separate source/priority from executor, route tree requests from either source to TreeGrow, keep command tools for accepted player goals outside native skills, and prohibit planner-to-LLM procedural fallback.

### Implement
- [x] Added docs/mod-development/native-runtime/HYBRID_DISPATCH_POLICY.md with the three channels, examples, ownership handoff, runtime checks and routing acceptance cases.
- [x] Updated architecture review, API requirements and document index to retain context-scoped LLM commands and prevent native cancellation/retry loops against player-priority actions.
- [x] Validated Markdown, JSON and links. This is a requirements clarification; runtime implementation is still outstanding. No game/runtime/DB/Redis changes or Git commit/push.

## 2026-10-24: Implement native hybrid runtime

### Investigate
- User explicitly authorized full implementation of the clarified three-channel architecture.
- Re-read project instructions/progress and inspected installed Task.stop/CommandExecutor bytecode plus TreeGrow and Auto Defense sources. Confirmed execute(String) requires the configured prefix, terminal user roots may persist after cancellation, and Task.stop rearms the same object's lifecycle.
- Read-only connection to Minecraft gateway 127.0.0.1:25333 was unavailable. No gameplay verification is claimed.

### Plan
- Keep LLM for player interpretation and guarded general commands; move background choices, native goal continuation, count tracking and combat ownership outside its loop.
- Adapt the actual installed artifacts through a pinned client-thread Java boundary rather than inventing nonexistent public APIs or editing the user's Lab mod projects.

### Implement
- [x] Added transactional SQLite GoalStore, request deduplication, priority queue, cancellation and one-runtime lease; data stays under ignored data/automation.sqlite by default.
- [x] Added Java ModGateway and Python NativeMods adapter: SHA-256 pins installed Fabric origins, client-executor operations, atomic defense enabling/ownership checks, bounded receipt ledger retained across Python reconnects, exact world/run identity, native command prefixes and no command chains.
- [x] Added same-object TreeGrow resume after observed defense interruption, retaining original time/site/search/budget; clear only its own retained terminal root. Re-enter native sapling acquisition after a failed site's consumed sapling. Death/world changes/manual cancellation do not authorize resume.
- [x] AutomationRuntime runs in MineBridge updater independently of LLM; scheduler only submits native tree goals. Legacy Python combat/water/navigation/autonomy writers are gated off in mods mode; native respawn and peaceful UnionClef exclusions remain.
- [x] Added grow_trees/get_mod_status/cancel_mod_goal tools and native prompt; retain guarded general player commands, context admission, trusted completion events and observations before claiming results. Hide/reject legacy microprocedures, suppress idle activation/telemetry-driven procedural calls, force ChatAgent for the native mode.
- [x] Added one external needs-based supplier for native equipment (healing potion components included), permissions/confirmation/cooldown without blocking defense. TreeGrow remains sole bone-meal supplier; old staff/XP preflight is not run in parallel.
- [x] Gradio shows actual mod owner, queue/progress/reasons/supply and supported skill probabilities; operator can queue counted trees and cancel. Unsupported background activities remain at 0. Confirmed planting can use existing varied speech without a model call.
- [x] Added env examples, read-only doctor checks, schema fields and native-runtime/IMPLEMENTATION.md. Legacy remains explicit opt-in; installed JAR changes require adapter compatibility review.
- [x] Validation: 441 Python tests passed, including 34 native queue/routing/Gradio/lost-response/death/supply cases; Java 21 compilation and offline ABI fixture test passed, including prefix handling, own/foreign task cancellation and death guard. New-module Ruff, undefined-name checks, AST parsing for 18 files, Markdown links/fences/UTF-8 and diff whitespace checks passed.
- [x] No game controls, live model calls, item grants, game/container restarts, mod JAR replacements, Lab edits or Git commit/push. Local adapter compilation and isolated test databases only. In-game acceptance remains NOT_TESTED; exact pinned versions and unsupported species/plant-only/custom-resource/magic capabilities are documented.

## 2026-10-24: Restore stopped supporting services

### Investigate
- User supplied doctor failures for TTS/STT/filter/Redis/PostgreSQL, while Minecraft and both mod APIs passed. Docker inspection found all five existing Saruman containers stopped.

### Plan
- Start existing containers without rebuild, replacement or volume deletion; verify application readiness and database credentials separately from TCP ports.

### Implement
- Started db, redis, filter, stt and tts-fish through compose.saruman.yml. PostgreSQL and Redis became healthy; SELECT 1 authenticated with the existing .env.saruman credentials. Filter /health confirmed models_loaded=true; STT /health and Fish /config returned HTTP success after Fish model warmup. No configuration/secrets changed, no volumes removed, no agent/gameplay or live LLM started.

## 2026-10-24: Remove legacy activities from native UI

### Investigate
- User noted the old autonomous activity list remained despite execution being mod-only. Native UI still reused the legacy catalogue/editor and showed unsupported activities at zero probability.

### Plan
- Give native mode its own panel: only Auto Defense and TreeGrow, a background TreeGrow switch and goal queue/history. Retain the old editor exclusively for explicit legacy mode.

### Implement
- Added a separate native Gradio panel and mod-only status rows; removed legacy activity weights/probabilities from that mode. The TreeGrow switch changes only its stored weight, preserving unrelated legacy settings. Updated runtime documentation and meaningful interface/settings tests: 47 focused tests passed.
- Current live agent_enabled is true. Did not interrupt gameplay or restart the running runtime to apply this UI-only change; a runtime restart is required to load the new layout. No Git commit/push.

## 2026-10-24: Fix native UI construction before bridge startup

### Investigate
- User screenshot after restart showed native data underneath legacy table headers/weight controls. Confirmed main.py constructs Gradio at module import, before main() initializes shared mode or MineBridge publishes telemetry. Prior UI tests incorrectly began with an already initialized native game state.

### Plan
- Choose layout from startup configuration explicitly, independent of bridge readiness, and freeze the panel's row schema to its selected layout.

### Implement
- main.py passes configured automation mode to add_autonomy_panel during initial construction and publishes shared mode before starting bridge processes. AutonomyPanel keeps the selected layout instead of switching row schemas through later telemetry. Removed the stale probability description from the main introduction.
- Added a regression test constructing the real native Gradio panel with an empty startup game, then attaching native telemetry and checking headers, row widths and absence of weight controls. All 48 focused tests and undefined-name checks passed. Running agent not interrupted; the corrected startup layout requires a new runtime process.

## 2026-10-24: Character voice, agency and remembered life

### Investigate
- User wants Saruman to speak as someone choosing and living his own life, without assigned-point/command/error reports, with a stronger character and personal recollections.
- Found mandatory narration of every meaningful action, report-like canned lines, obsolete combat/scepter rules preceding the native runtime boundary, no backstory bank in the cached ChatAgent prompt, and native speech ignoring the editable PostgreSQL bank. The historical speech migration imported mutable live seeds.

### Plan
- Rework personality and stock voice together; keep real progress/observations honest while making motives and reactions personal. Add contextual fictional prehistory without extra LLM planning calls, keep manual DB edits, and reduce repeated automated narration.

### Implement
- Rewrote the shared YAML personality: initiative, pride/curiosity/self-irony, natural acceptance of requests, permission for silence, private technical telemetry, plain technical answers only when asked, and truthful distinction between intention/result/abandonment. Removed outdated execution instructions from the personality core.
- Reworked all 52 stock phrases and authored 48 concrete prehistory scenes across eight topics. Fictional scenes are explicitly distinguished from Tolkien canon/current-server memories; generic phrases avoid invented terrain and unconfirmed growth/victory.
- Added a balanced bounded prehistory selection to the cached ChatAgent prompt, respecting edited/disabled database records. Native speech now loads the same startup DB bank and publishes recent spoken phrases for conversational repetition avoidance.
- Peaceful canned speech has a 30-second minimum interval. Optional lore is topic-matched with its own time budget and randomized spacing, never during combat; the every-fifth-action rule is removed. No additional LLM calls were introduced.
- Froze historical v1/v2 catalogue snapshots and added migration c9d0e1f2a3b4: only untouched defaults are refreshed; manual text/kind/topic edits and disabled flags survive; new scenes are inserted idempotently. Added Git-ignore exceptions so runtime bank and migration snapshots are included in the user's eventual Git changes.
- Added character README with speech examples, data ownership, fiction boundaries and restart behavior; updated idea prompts and runtime speech instructions.
- Validation: 449 relevant Python tests passed, including character loading, actual migration upgrade/downgrade/edit preservation, topic/rate-limit behavior, native DB speech, cached personality grounding and existing game/runtime tests. Ruff/undefined-name checks, AST, YAML alias checks, JSON v2 consistency (48 scenes/eight topics) and diff whitespace passed.
- Separate broad ProfileTests run exposed two unrelated existing mismatches: env templates differ by a local CODEX_HOME entry, and a command-rejection assertion expects wording 'forbidden' absent from its existing error message. These files/behavior were not changed for this task. The character contract test itself passes.
- No live LLM generation, gameplay, DB migration execution or running-agent restart. The next full local-runtime startup applies the data migration and reloads persona/voice caches; Docker rebuild is unnecessary. Behavioral style still requires observation in actual dialogue, not a claim of guaranteed model output.
