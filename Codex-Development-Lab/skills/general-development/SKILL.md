---
name: general-development
description: Build or modify any software project from its own specification, with incremental implementation, reproducible dependencies, and honest status reporting.
---

# General development

- Treat the current project's `ТЗ.md` as the source of **project-specific functionality**. Do not import requirements from previous projects, examples, other Skills, or unrelated documents.
- Choose the simplest design that satisfies the specification. Implement observable functionality in small, testable increments; prioritize a working main scenario over optional refinements.
- Use the target platform and versions given in the specification. Pin compatible dependencies; use installed sources or authoritative documentation to confirm uncertain APIs.
- Maintain or create a reproducible build. Fix failures rather than masking them or silently relaxing requirements.
- Respect the project environment, relevant repository instructions and the platform's safety boundaries. Never publish, purchase, expose credentials, or modify secret files without explicit authorization.
- At the end of a development step, state what changed, what was actually verified, and what remains incomplete. An unexecuted test or an unobserved feature is not success.
