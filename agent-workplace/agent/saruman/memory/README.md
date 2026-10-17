# Memory — Saruman

Write working memory in English. Preserve names exactly, and keep direct quotes in
the language in which they were spoken. PERSONA.md describes a fictional identity;
it is not evidence that an encounter or a game action actually happened.

## Foundational

The operator owns the fixed core of ../PERSONA.md. Refine only its explicitly
editable section, rarely and deliberately. Mood and relationships belong here in
memory, not in the fixed character definition.

## State

Rewrite state.md at the end of a session. Keep current mood, current goal,
confirmed location, important relationships, open promises, and missing capabilities.
Mark unknown values as unknown. Keep hypotheses distinct from observations.
Compress old detail instead of appending a transcript forever.

## Entities

Use one file per person, place, or other important entity in entities/. Prefer stable
IDs such as player UUIDs to filenames based on mutable display names. Store the
verbatim name inside the note. Create notes only after actual observations or
operator-supplied facts, and identify the source. No example acquaintances are seeded.

Use this shape (placeholders below are a format example, not a real encounter):

```markdown
# <verbatim name>
type: player | id: <stable-id> | met: <observed-date> | where: <observed-place>

## Events
- <observed-date> — <confirmed interaction and source>

## Relationship
Neutral; no earned trust or recorded grievance yet.

## Open promises
None.

## Links
- [[saruman]] — interacted with this character
```

Use double-bracket links to existing entity-note filenames without .md, with links
in both directions. The self-link [[saruman]] needs no entity file. Add each new note
to MEMORY.md, preserve dates and quotes, and update relationships from events.
Do not invent reciprocity: an animal hearing a monologue has not necessarily replied.

Keep noteworthy confirmed events as dated entries in the relevant entity notes.
They can later supply stories and video descriptions; they are not publications.
Do not retain every movement tick or recomputable inventory snapshot as long-term memory.
