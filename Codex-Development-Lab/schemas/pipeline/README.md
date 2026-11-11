# Runtime pipeline contracts, version `v1`

Current role context, evidence access, optional action outcomes, stable findings
and execution reuse: [optimized runtime](../../docs/OPTIMIZED_RUNTIME_RU.md).
Historical objects remain readable. Optional additions are explicit in new strict
submissions; canonical metadata still belongs to the controller.

Each `<Name>.schema.json` is a self-contained JSON Schema Draft 2020-12.
`examples/<Name>.json` contains one shape-valid example. Example hashes and
project paths are illustrative; they do not prove any real implementation.

`common.pipeline_contracts` loads only these image-owned files. Its `validate`
function returns the original object or raises `ContractError` with exact field
paths, expected types/values and examples. It never repairs JSON or grants PASS.
`feedback` supplies adaptive correction guidance without a fixed correction cap.

`<Name>.submission.schema.json` is the portable generation grammar used by the
CLI and native API tools. It preserves types, required fields, enums and exact
object shape. Provider-specific decoder limitations do not remove constraints:
the controller always checks the full `<Name>.schema.json`, file paths and graph
after receiving the result. `read_output_format` returns the full trusted schema.

`common.task_graph` separately validates declared requirements/conditions,
traceability, the DAG, action dependencies, and scenario/JUnit bindings. The
controller must also check actual file hashes, observed citations, execution
origin, current source/spec revisions, permissions and budgets. LLM review owns
semantic completeness; independent execution owns observed test results.

Additional concrete runtime bindings relative to the design examples:

- `Scenario.execution` selects `gameplay`, `unit` or `integration`, a safe Gradle
  task, expected JUnit case identities, and one distinct case per checkpoint.
- `ReviewResult.reviewed_condition_ids` names the conditions actually inspected.
  `evidence_refs` cites the actual source read, even when there are no findings.
- `ArtifactRef.path` is a canonical relative path. Filesystem readers must still
  enforce root confinement and reject symbolic-link escapes.
- `PlannerDecision.regression_condition_ids` chooses affected existing conditions
  to rerun. It has no fixed count. Final verification always runs the full manifest.

For explicit maintenance after changing the design files, run
`python schemas/pipeline/generate_from_design.py`. Job processing never runs this
generator and never takes schemas from a project workspace.
