# Codex CLI invocation audit evidence

The project pins `@openai/codex@0.162.1` in `Dockerfile.worker:2`.
`agent/daemon.py:27-28` constructs:

```text
codex exec --skip-git-repo-check --sandbox danger-full-access --ask-for-approval never --json ...
```

## Evidence for the pinned source version

Primary source files at the exact `rust-v0.162.1` tag:

- https://raw.githubusercontent.com/openai/codex/rust-v0.162.1/codex-rs/exec/src/cli.rs
- https://raw.githubusercontent.com/openai/codex/rust-v0.162.1/codex-rs/utils/cli/src/shared_options.rs

The exec parser flattens `ExecSharedCliOptions`, which uses `SharedCliOptions`.
Neither file declares `--ask-for-approval`. The shared parser declares `--sandbox`,
`--approve-for-me`, and `--dangerously-bypass-approvals-and-sandbox`.
The exec parser's other fields do not add an approval argument.
Thus the daemon argument is incompatible with its pinned CLI parser.
This source check was read-only and did not start a Codex session.

## Additional local parser reproduction

The installed local CLI is a different build:

```text
> codex --version
WARNING: proceeding, even though we could not create PATH aliases: Could not find home directory
codex-cli 0.162.0-alpha.17.2
```

The following command includes `--help`, so it cannot start a real Codex turn:

```text
> codex exec --skip-git-repo-check --sandbox danger-full-access --ask-for-approval never --json --help
WARNING: proceeding, even though we could not create PATH aliases: Could not find home directory
error: unexpected argument '--ask-for-approval' found

  tip: to pass '--ask-for-approval' as a value, use '-- --ask-for-approval'

Usage: codex exec [OPTIONS] [PROMPT]
       codex exec [OPTIONS] <COMMAND> [ARGS]

For more information, try '--help'.
```

Local alpha behavior corroborates the pinned source analysis; it is not presented
as an execution test of the pinned production binary. Actual Docker worker build
and authenticated Codex execution remain untested by this reviewer.

## Suggested correction

Use the supported configuration override `-c approval_policy="never"` and retain
the separately chosen sandbox policy, then add an offline parser compatibility
check against the exact installed `codex` version. This is a proposed correction;
this audit did not modify application code.
