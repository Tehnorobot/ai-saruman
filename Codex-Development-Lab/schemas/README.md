# Planner output contract

`planner_output.schema.json` is the canonical, image-owned Draft 2020-12 JSON
Schema for a raw planner proposal. `common.output_contracts` loads it relative to
its own installed code, never from the user workspace or an environment override.
Ship `schemas/` beside `common/` in the worker image. There is no generated second
schema or provider-specific contract to keep in sync. Native tool input schemas
must preserve the complete document, including `$defs` and local `$ref` entries.

Call `validate_planner_structure(parsed_value)` after strict JSON decoding and
before `common.planning.validate(...)`. Successful structural validation returns
the original object; errors are `PlannerStructureError`, a `ValueError` with at
most eight bounded JSON paths and no echoed input values. `load_planner_schema()`
returns a defensive copy for native tool definitions. Invalid JSON, including a
missing closing action brace, must be rejected or regenerated, never repaired by
moving fields or guessing intent. If valid JSON accidentally puts `hypotheses`,
`next_steps`, reviews, or package fields inside `action`, the strict action shape
rejects it.

Only `summary` and `action` are required at the root; only `kind`, `target`, and
`reason` are required in each action. Optional fields may be omitted rather than
filled with null. Root and nested records reject unknown fields. An ordered
package uses `additional_actions` (at most three), a root `bundle_reason`, and an
optional `verification_limit` from one through four. Only supplementary actions
accept `depends_on`; dependencies name earlier action targets. The controller
checks their order, scope, counts, and applicability.

Examples cover every supported primary action kind:

| File | Action kind | Necessary controller context |
| --- | --- | --- |
| `examples/implement.json` | `implement_and_map_requirement` | Current unresolved requirement and exact source condition |
| `examples/repair.json` | `repair_scenario` | Known independently failed scenario and observed cited evidence |
| `examples/verify.json` | `implement_or_verify_scenario` | Known unverified or stale scenario |
| `examples/diagnose.json` | `diagnose_failure` | Observed failure; concrete hypotheses for deep diagnosis |
| `examples/review_coverage.json` | `review_coverage` | Exact immutable condition inventory, mapped scenarios and observed source citations |
| `examples/repair_verification.json` | `repair_verification_failure` | Independent build or verification infrastructure failure |
| `examples/repair_acceptance.json` | `repair_acceptance_plan` | Controller-reported acceptance-plan error |
| `examples/manual.json` | `await_user_review` | Genuinely manual, unmapped requirement with no executable scenarios |
| `examples/bundle.json` | Ordered implementation package | Related in-scope targets; no mandatory single-action directive |

Examples are structurally valid fictional proposals, not evidence of execution.
Their condition IDs, requirement IDs, excerpts and paths must be replaced by real
controller context and tool-observed source. A structural pass never grants a
semantic waiver: task scope, scheduling directives, condition identity, package
dependencies, progress policy, coverage mappings, citation line ranges and exact
observed excerpts still require the existing controller validators. A covered
proposal cannot waive real assertions. Manual review cannot hide executable work,
failed tests, build failures, or provider failures. Independent verification owns
all test statuses and evidence.

API planners use `submit_planner_plan` with this schema as its input contract.
`read_planner_format` retrieves one named example on demand. Codex CLI returns
final JSON, checked against the same schema by the controller. Contracts and
examples come only from the installed image; workspace copies cannot override them.
