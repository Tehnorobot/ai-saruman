# 2026-11-11: Telegram events, persona drafts, durable outbox and operator window

## Investigate

- Confirmed the owner's successful VPS deployment: Ubuntu 24.04/Python 3.12,
  Telegram bot/channel rights, real publication, duplicate suppression, systemd
  startup with retained message_id and PC-to-VPS SSH tunnel publication.
- Root Character Lab and Minecraft runtime have separate Python environments and
  memories. Reused the native dispatcher outcomes, PersonaAgent role profile and
  usage accounting. No Telegram client was previously wired into the character.
- Optional publishing must never block player replies, goal cancellation, startup
  or game controls. Mod completion can occur between slow persona observations.

## Plan

- A shared standard-library module owns a durable local event/draft/outbox journal.
  Capture confirmed native outcomes before run IDs are cleared. Feed a small batch
  of facts and recent public posts to the existing persona model, keeping raw history.
- One controller lease per stage and stable publication IDs coordinate Lab/runtime
  workers. Preserve unknown deliveries for human reconciliation, retry known 429s
  with Retry-After, isolate failures to the post/optional component.
- Provide Telegram controls in the existing lab, optional key-based tunnel recovery,
  and an installation guide. Default to disabled/review mode; no automatic paid calls
  or live publications during implementation. Keep Mage Home excluded.

## Implement

- Added integrations/telegram-gateway: bot/channel checks, restricted text publisher,
  durable receipts, API v2 channel updates and safe retries of rejected requests,
  systemd/Docker deployment and tests. VPS token remains server-side.
- Added shared automation journal, explicit event references, durable drafts/outbox,
  attempt history, channel inbox/cursor, paginated inspection and two-stage enable/
  autopublish controls. Failed/invalid drafts release their source events for retry.
- Hooked confirmed per-tree completion into AutomationRuntime.change, including
  background goals. Snapshot fallback deduplicates by world/run; incomplete/failed
  growth is never success, stale/out-of-world snapshots cannot create achievements,
  ending defense is not proof of a kill. Excluded chats/player names/coordinates.
- Added runtime Director with a separate RoleClient using the same persona profile,
  session suffix :telegram and existing usage accounting. Model failure does not
  block the public PersonaAgent. Stop, cleanup, telemetry/disk failures are isolated.
- Added Telegram lab view and CSRF-protected settings/draft/action endpoints, source
  and attempt inspection, manual review and queueing. Polling does not replace a
  draft being edited or expanded diagnostics. Show persona model separately from
  the chat lab model. Secrets stay outside HTTP responses and model context.
- Added managed strict-host-key SSH process and setup_tunnel.py: one interactive
  owner-run provisioning of a restricted, non-root tunnel user/public key, preserving
  the existing local API key. No changes to root login/global sshd configuration.
- Validation: 165 tests passed, 1 Windows symlink privilege skip; JS rendering/XSS
  checks and headless Edge page render passed. No real LLM generations, game changes,
  private-key reads or new live publications. Existing asyncio warnings are unchanged.
- Live update remains owner-applied: BatchMode SSH to VPS denied key authentication.
  See integrations/telegram-gateway/AUTONOMOUS_RU.md. Existing .env and receipt DB
  are retained; only gateway.py needs replacing on VPS before restarting its service.
