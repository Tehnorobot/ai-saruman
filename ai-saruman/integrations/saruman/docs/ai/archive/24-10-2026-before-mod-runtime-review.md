# Progress

Previous work: [universal mod design and native movement feasibility](archive/20-10-2026-universal-defense-design.md).

## 2026-10-20: Minecraft 1.21.11 implementation guide

### Investigate
- User requests an additional Markdown with concrete implementation rules for reliable native combat in the current game version.
- Re-read project instructions/progress and existing specifications. Reviewed official version-specific Yarn APIs for KeyboardInput, PlayerEntity, interaction manager, item components and BowItem; Fabric 1.21.11 tick/command samples and versioned testing instructions.
- Read-only installed metadata: Minecraft 1.21.11, launch Java major 21, Loader 0.19.2, Fabric API 0.141.3+1.21.11, UnionClef 1.21.11-0.95.61, Magical Scepter 1.2.0. Recorded UnionClef SHA-256, without executing game actions.

### Plan
- Add a standalone engineering guide complementing the functional spec: reproducible version profile, invariants, input-consumption timing, immutable snapshots, single lease/frame, source-backed interaction flow, native movement, compatibility isolation, and staged evidence-driven validation.
- Explicitly distinguish inspected facts, proposed implementation choices and untested gameplay; no promise of perfect behavior across arbitrary servers/mod combinations.

### Implement
- [x] Created AUTO_DEFENSE_IMPLEMENTATION_GUIDE_1_21_11.md with 18 sections and implementation/release checklist; its current location is docs/mod-development/auto-defense/.
- [x] Included special handling for selected-slot synchronization without invented ACK, vanilla attack bookkeeping/reach/components, bow cancellation without accidental release, use-channel arbitration, UnionClef hooks outside TaskRunner, stale requests and cleanup, game tests and controlled latency scenarios.
- [x] Linked the guide from both existing specs and clarified that the client thread prohibition concerns blocking network/disk I/O, not normal game packet dispatch.
- [x] Validated numbering, Markdown fences, JSON examples, UTF-8, local links and documentation whitespace. No runtime code changed; no gameplay tests, build/install, game commands, Redis/DB mutation, restart or Git commit/push.

## 2026-10-20: Dedicated mod documentation folder

### Investigate
- User requests exact document paths and a separate folder for future mod-development Markdown files.

### Plan
- Move the three current mod documents together, preserve relative cross-links, add a reading-order README and record the location convention in AGENTS.md.

### Implement
- [x] Moved all three active documents from docs/dev into docs/mod-development/auto-defense/ without retaining duplicate specs.
- [x] Added README.md explaining required universal spec/guide versus optional Saruman integration; linked it from docs/README.md and recorded future docs/mod-development/<mod-name>/ convention in AGENTS.md.
- [x] Checked moved-file content, Markdown/JSON, links and whitespace. Historical archive paths remain as historical records. No runtime or Git mutations.
