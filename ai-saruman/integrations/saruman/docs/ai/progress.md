# Progress

Previous work: [mod implementation guides and document organization](archive/24-10-2026-before-mod-runtime-review.md).

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
