# Progress

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
