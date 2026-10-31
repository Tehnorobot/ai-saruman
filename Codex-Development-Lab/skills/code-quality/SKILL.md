---
name: code-quality
description: Implement and debug software with minimal coherent changes, clear ownership of state, and verification of real behavior.
---

# Code quality

- Before changing code, trace the relevant callers, state transitions and dependencies. Reproduce a reported failure when possible.
- Prefer the smallest complete fix or implementation. Do not introduce extra frameworks, subsystems or public interfaces without an actual requirement.
- Follow existing conventions; avoid unrelated refactors. Remove unused code created by the change.
- Treat cancellation, timeouts, repeated events, error recovery and resource cleanup when they are relevant to the feature.
- For versioned third-party APIs, verify signatures and behavior against the version actually in use. Do not invent integration methods.
- Validate observable outcomes, including failure paths. Add focused regression checks when valuable; do not alter tests merely to get a passing result.
- Distinguish implementation progress from independently verified correctness.
