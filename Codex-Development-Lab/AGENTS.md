# Permanent rules for all Codex agents
1. A single `ТЗ.md` is the source of feature requirements. Do not invent passing criteria or alter stated restrictions to obtain PASS.
2. Read installed skills on demand. If mod is Fabric 1.21.11, read the bundled implementation guide.
3. Create buildable source code, real tests and a reproducible project. Pin dependencies.
4. Use specialist subagents for independent review/test planning, but don't let two subagents modify the same file at once.
5. Never mark tests PASS yourself. Independent verifier owns statuses and evidence.
6. No git push, releases, game EULA acceptance, purchases, modification of .env or reading secret storage.
7. Finish each pass with concrete changes; failures should be reproduced, not hidden.
8. External service APIs and network calls are out of the critical game tick. Verify all target constraints.
9. Do not claim a production JAR is ready unless Gradle succeeded and resulting artifact exists.
