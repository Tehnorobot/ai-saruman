---
name: testing-evidence
description: Validate software through independent, executable checks and authentic logs, screenshots or recordings when supported by the test environment.
---

# Testing and evidence

- Check the observable behavior required by the project's specification, not just the presence of source files, method names or a successful compilation.
- Run real builds and executable tests when the environment permits. Report failures and unavailable test environments distinctly.
- Assign PASS only when a relevant check actually completed and its observed result satisfies the scenario; use FAIL for demonstrated failures and NOT_TESTED when verification was not run or is inconclusive.
- Prefer independently produced machine evidence. An implementing agent's statement that something works is not proof.
- Screenshots and video may be attached only if captured during a real test; label them with the actual run and scenario. Never fabricate or simulate verification media.
- For graphical/game software, use an actual application session for interaction tests where feasible. Unit tests and compilation alone do not prove interactive behavior.
- Recording limits, storage destinations, credentials and upload retries are managed by the lab's services/configuration, not by individual project specifications.
