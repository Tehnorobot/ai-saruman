# 2026-11-11 — Unified character lifecycle, Telegram and private control

## Investigate

The owner deferred mod work and approved all five autonomy steps. Existing Lab
was dialogue-only, while Telegram drafting required a fresh game-world heartbeat.
The game needed its own launcher and Gradio Start. The gateway accepted only a
fixed channel; DM/comment routes, scoped person memory and private admin control
did not exist. Retained native mods and the user's unrelated memory/spec edits.

## Plan

Reuse the separate Python environments, role database, Telegram outbox, leases,
native API and existing game supervisor. One authenticated local lifecycle owner;
independent failures; no LLM for dispatch/delivery/lifecycle. Separate private admin,
public posts and chat-scoped Telegram memory. Confirm world entry before actions.
No real publications, game-world changes, account login or VPS mutation during tests.

## Implement

- Lab Start/Stop and component states available on every page. OS-backed owner
  locks prevent duplicate character/game runtimes. IPC uses fixed project paths,
  revisions, a worker identity and an expiring heartbeat. Start launches the
  blocking supervisor on its own thread; Stop stays responsive. Lost owner pauses
  gameplay; graceful Lab exit requests shutdown of its own game process.
- Telegram draft/delivery/reply workers are Lab-owned. Game observation continues
  recording verified facts but cannot become a competing sender. Persistent
  schedule survives Stop/Start. Scheduled reflections are permitted without game
  claims; pending review prevents duplicate background drafts. Epoch checks keep
  stale generation results from auto-publication after Stop/Start.
- Gateway API v3 persists eligible incoming updates before Telegram acknowledgement.
  Only actual human DMs and the configured channel's linked discussion group can
  become reply targets. sendMessage destination/reply/thread come from that ledger,
  never an LLM-supplied chat_id. Persistent publication IDs and unknown-send
  handling apply to replies too. VPS deployment remains owner-operated.
- Scoped Telegram people/history by stable user_id and DM/group-thread. Full
  transcript retained in SQLite; summary/recent history used for replies; paged
  inspection in Lab. No DM/admin material is included in diary generation.
- Private admin page, idempotent command log, allowlisted operations, direct
  buttons without LLM and model-driven command interpretation. Separate admin
  and telegram_reply role profiles/accounting. No personality-based refusal in
  trusted game control; native safety/argument validation remain. Private game
  requests preempt prior requests, suppress public persona/TTS and hide private
  command goals from external model context, including after restart.
- Saved Minecraft server/existing-world/optional launcher settings. Server join
  uses verified UnionClef ConnectToServer; local world uses Minecraft 1.21.11's
  existing-save loader on its executor. No world creation/EULA/login automation.
  Existing worlds are not closed automatically. Launchers need an operator-chosen
  executable/argument list; unsupported launch behavior is not guessed.
- Corrected test fixtures that copied the owner's live learned memory and left a
  SQLite connection open on Windows. No owner memory was deleted or staged.

## Validation

Offline checks cover HTTP/CSRF, IPC ownership/recovery, generation Stop/Start,
private admin boundaries, destination spoofing, linked discussion/thread routing,
per-user memory isolation, response-loss reconciliation, schedule persistence and
JVM existing-world loader contract against isolated Java doubles. Final relevant
checks: 56 Lab tests (55 passed, one Windows symlink-privilege skip), 68 Telegram
tests passed, 105 game role/dispatcher/preflight tests passed; two private-boundary
checks also rerun after the last privacy filter change.
Browser smoke used an isolated Chrome profile, temporary database and fake models:
Start/Stop, Telegram without game, private admin form, role selectors and connection
settings passed; no JS exceptions/mobile horizontal overflow. Real API responses,
voice behavior, launcher-specific Play and actual world entry remain manual checks.

The broader old test_saruman/test_memory_reset run also exposed existing failures
(old error-message assertion and reset fixtures using outdated profile revisions).
They were not relaxed or changed to claim success. Relevant role/mod suites pass.

User guide and manual verification: [docs/AUTONOMY_RU.md](../../../../../docs/AUTONOMY_RU.md).
Local diagnostics/screenshots: .saruman/audit/runtime-ui (ignored).
