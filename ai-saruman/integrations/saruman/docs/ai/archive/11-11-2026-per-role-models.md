# 2026-11-11 — Shared per-role models in Saruman Lab

## Investigate

- Lab chat used its own Codex selector. The game persisted only persona/action
  profiles; Telegram borrowed persona. Legacy agents and helper tools constructed
  clients through environment-based factories, outside the role editor.
- Read role settings/accounting, native game boundary, legacy LangChain adapter,
  Lab HTTP/JS, Telegram bridge and their tests. Reused the existing RoleStore.
- Verified MiniMax M Plan and M3.1 Flash against primary documentation:
  [subscription key](https://platform.minimax.io/docs/m-plan/quickstart),
  [Anthropic protocol](https://platform.minimax.io/docs/api-reference/text-chat-anthropic).
  Explore accepts a Subscription Key; it is distinct from a pay-as-you-go key.
  Flash always thinks; output_config.effort supports low/medium/high/xhigh/max,
  with max as the provider default.

## Plan

- One SQLite configuration shared by both Python environments. Keep game
  dependencies out of the stdlib Lab. Share pure protocol/settings code.
- Expose four primary consumers, with legacy/helper consumers collapsed and
  labelled. No extra model calls or automatic tools when opening/saving settings.
- Preserve old profiles and history; give Telegram its own role. Validate
  provider/model/reasoning, protect running calls and stale browser edits.
- Keep credentials in the existing local environment file, outside the UI/SQLite.
  Do not fall back to another provider after access or output failures.

## Implement

- Shared role_settings.py now describes lab_chat, persona, action, telegram,
  legacy_core/planner/reaction/chat/browser and tool_dialogue/nickname/vision.
  Existing Telegram model is copied from persona once. Subsequent choices are
  independent. Session limits remain editable with no arbitrary upper UI caps.
- Shared role_protocol.py builds MiniMax/DSLab/LiteLLM requests, transforms image
  input, validates endpoint configuration, decodes results and normalizes usage.
  MiniMax uses its native Anthropic-compatible URL, adaptive thinking and bearer
  subscription key. Incomplete responses retain observed usage.
- Game RoleClient and legacy RoleChatModel route to saved profiles before each
  request. Legacy factories are wired to their actual role, not decorative fields.
  No game tools are delegated to the remote provider. Role histories keep actual
  Codex model selection/reroute and separate provider names.
- Lab roles.py imports only the two stdlib shared modules, reads the same
  credential file and uses the same profile DB. Chat routes through lab_chat.
  The browser cannot override the configured provider with a chat payload.
- /api/models uses existing CSRF/origin checks. Profile changes check revisions
  transactionally and cannot replace a running request's configuration. Other
  roles remain editable. An interrupted dead process can be recovered safely.
- Settings now has provider/model/reasoning selectors, short role guidance,
  optional legacy cards, editable limits, credential instructions and recent
  role requests. Codex model/effort metadata is loaded from model/list and cached.
  UI controls distinguish configured credentials from verified model access.
- Root run storage migrates additively to retain provider/role/reasoning and
  cache writes. Anthropic input totals include cache once, with no double count.
- Updated both environment templates, root/runtime READMEs, Telegram instructions
  and docs/MODEL_SETTINGS_RU.md. Local credentials were not edited or printed.

## Validation

- Lab: 32 tests, 31 passed; one existing Windows symlink privilege skip.
- Role agents/routing/Telegram bridge: 57 tests passed.
- Native mod runtime regression: 43 tests passed.
- Telegram gateway/automation/provisioning: 52 tests passed, no real publications.
- Broader test_saruman.py: 88/89 passed. Existing failure
  test_game_action_rejects_prose_unknown_commands_and_command_chains expects the
  word "forbidden" in a command rejection; current validator emits a different
  rejection message. minecraft.py and that assertion are unchanged from HEAD.
  The boundary still rejects the command. No unrelated policy change was made.
- Browser CDP against a temporary local database: all 12 role controls rendered;
  MiniMax model and six effort options; save/reload persistence; independent action
  role; zero LLM requests; no horizontal overflow at desktop and 390px mobile.
  Screenshot remains local at .saruman/roles-ui-proof.png. JS and Python syntax,
  UTF-8 and whitespace checked. Temporary browser/server isolated from real state.
- Live MiniMax authentication/quota was not tested without the owner's key.
  Full game/social behavior with selected models remains an owner integration check.
  Existing asyncio/Pydantic deprecation/resource warnings remain in legacy tests.

## Apply

Stop the old Lab and game Python processes once, then start the updated versions.
Minecraft and the VPS gateway need no restart for these model settings. Add a
Subscription Key to integrations/saruman/.env.saruman; restart Python processes
after environment changes. Select each role at http://127.0.0.1:8765/#settings.
Later profile changes apply to the next request. Existing running calls are not
interrupted. Unrelated Mage Home specification changes remain separate.
