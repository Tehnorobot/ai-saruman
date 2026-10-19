# ReactionAgent — Legacy Chat Agent

**File:** `src/agent/multi/reaction_agent.py`

## Overview

Lightweight agent that reacts to chat events with personality-driven responses.
NO tool usage — only calls `speak` directly. Designed for fast, immediate responses.

## Prompt Trace

```
ReactionAgent.step()
  -> wait_for_sync(timeout=15s)  # wait for chat event trigger
  -> ReactionAgent.react(trigger_event)
    -> CharacterManager.read_character_prompt(personality)
    -> format_events_with_roles(events, tool_call_format="text")
    -> get_users_info() / get_game_status()
    -> build messages list
    -> llm.stream(messages)
    -> tool_bank["speak"]`raw_response_text`
```

## Message Structure (what LLM sees)

```
messages = [
  {role: "system",    content: "{character_prompt}\n\nYour output format is just text to speak *emotion*..."}
  {role: "user",      content: "# Current Situation:\n## Game Status:\n...\n## Nearby Users:..."}
  {role: "user",      content: "DIVIDER: acknowledge Old events..."}       # <-- PROBLEM: divider as user msg
  {role: "user",      content: "MINECRAFT CHAT [2m ago] User1: hello"}     # <-- PROBLEM: multiple user msgs
  {role: "user",      content: "MINECRAFT CHAT [1m ago] User2: yo"}
  {role: "assistant",  content: "privet *happy*"}                           # <-- OVERRIDE_ASSISTANT_ROLE
  {role: "user",      content: "DIVIDER: react to Recent events..."}
  {role: "user",      content: "MINECRAFT CHAT [2s ago] User1: cool\n\nResponse reminder: ..."}
]
```

## Known Problems with Modern LLMs

### 1. Broken user/assistant alternation
Multiple consecutive `user` messages. Claude API requires strict alternation.
LiteLLM may merge or error.

### 2. Dividers as user messages
`format_events_with_roles()` emits dividers like "acknowledge Old events" as `role: "user"`.
Modern LLMs treat these as user requests and try to respond to them literally.

### 3. No response parsing
Raw LLM output goes directly to `speak()`. Modern LLMs add preambles, markdown,
explanations — all goes to TTS as-is.

### 4. Events as separate messages
Each event is a separate `{role: "user/assistant", content: "..."}` message.
Should be consolidated into a single text block.

## Replacement

See `CHAT_AGENT.md` for the modern replacement with native function calling.
