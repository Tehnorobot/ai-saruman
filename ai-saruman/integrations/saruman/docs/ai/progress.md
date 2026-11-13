# Progress

## 2026-11-13: Keep the tree workflow pending during native scheduler recovery

### Investigate
- Latest private workflow c2dba6de entered test_3 and started TreeGrow. Native
  Unstuck Chain (priority 55) interrupted the user task (50). Auto Defense was
  not the interrupter; the saw_defense prerequisite marked this scheduler
  cancellation as failure. The later photo/publication steps correctly stayed pending.
- Native live read confirms scheduler interruptions with zero planted trees and
  remaining native budget. Idle Mob Defense may be selected with priority zero;
  treating every active foreign chain as busy would deadlock resumption.

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
- Installation requires a native worker restart; current character remains active.

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
