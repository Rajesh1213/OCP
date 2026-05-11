# Contributing to the Open Context Protocol

## Welcome

Thank you for your interest in contributing to the Open Context Protocol (OCP).
OCP has two distinct contribution surfaces: the **reference implementations and
tooling** under `packages/` (licensed Apache-2.0) and the **protocol
specifications** under `spec/` (licensed CC-BY-4.0). Both are developed in
this repository, but they have related yet different contribution processes,
which this document explains in full.

For background on what OCP is, what problem it solves, and how the pieces fit
together, start with [README.md](./README.md).

All contributions — code, specification text, tests, documentation — are
welcome. The bar for acceptance is quality, spec-correctness, and
maintainability, not novelty. Incremental improvements, bug fixes, and
conformance test additions are just as valued as new features.

---

## Code of Conduct

This project follows the [Contributor Covenant v2.1](./CODE_OF_CONDUCT.md)
(to be added to the repository). All contributors, maintainers, and community
members are expected to abide by it in all project spaces: GitHub Issues, Pull
Requests, Discussions, and any associated chat or mailing lists.

Reports of unacceptable behaviour should be directed to the maintainers via the
contact method specified in `CODE_OF_CONDUCT.md`. All reports will be treated
with confidentiality.

---

## Reporting Issues

**Bugs, feature requests, and specification questions** are tracked in
[GitHub Issues](https://github.com/Rajesh1213/OCP/issues). Before opening a
new issue, search existing ones to avoid duplicates.

Use the appropriate issue template when one is available:

- **Bug Report** — something in the reference implementation does not behave
  as the specification requires, or crashes unexpectedly.
- **Feature Request** — a capability that is absent and would be useful.
- **Spec Question** — ambiguity or a gap in the specification text itself.

Include as much context as you can: the OCP version, Python version, operating
system, the exact command or tool call that triggered the problem, and the full
error output.

**Security vulnerabilities must not be disclosed in public issues.** If you
discover a security vulnerability, do not open a GitHub Issue. Instead, follow
the process described in `SECURITY.md` (to be added). Until that file is
present, contact the maintainer directly at the email address on the repository
profile. You will receive a response within 72 hours acknowledging receipt.

---

## Contributing Code

### Workflow

OCP uses a standard fork-and-pull-request workflow on GitHub:

1. Fork the repository to your own GitHub account.
2. Clone your fork and create a feature branch off `main`:

   ```
   git checkout -b feat/my-improvement
   ```

3. Make your changes. Write or update tests as needed (see
   [Testing Requirements](#testing-requirements)).
4. Commit your changes following the conventions below.
5. Push your branch to your fork and open a Pull Request against
   `Rajesh1213/OCP:main`.

### Developer Certificate of Origin (DCO)

**Every commit must carry a `Signed-off-by` trailer.** This is enforced by the
DCO check in CI. To add the trailer automatically, use the `-s` flag:

```
git commit -s -m "feat: add workspace.search_by_symbol tool"
```

This produces a trailer like:

```
Signed-off-by: Your Name <you@example.com>
```

By signing off, you certify that you wrote the contribution or otherwise have
the right to submit it under the project's license, as described at
<https://developercertificate.org/>. You are not assigning copyright — you are
attesting that the code is yours to contribute.

If you forget to sign off, amend the commit before pushing:

```
git commit --amend -s
```

For a branch with multiple unsigned commits, use:

```
git rebase --signoff HEAD~N
```

where `N` is the number of commits to sign.

### Commit Message Format

Commit messages must follow the
[Conventional Commits](https://www.conventionalcommits.org/) specification.
The format is:

```
<type>(<optional scope>): <short description>

<optional body>

<optional footers>
Signed-off-by: Your Name <you@example.com>
```

Accepted types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`,
`ci`. The short description must be in the imperative mood and must not end
with a period. Keep the first line at or under 72 characters.

Examples:

```
feat(server): add state.list cursor-based pagination
fix(storage): resolve chunk ID mismatch on macOS symlinked paths
docs: clarify scope resolution order in §5.1
test: add conformance test for session.restore state copy
```

### Pull Request Requirements

A PR is ready for review when all of the following are true:

- The CI suite passes: linting, type checking, unit tests, and the OCP-0002
  conformance pack all green.
- Conformance coverage does not decrease. If your change touches normative
  behaviour, a corresponding conformance test must be in the same PR (see
  [Testing Requirements](#testing-requirements)).
- The PR description explains *what* changed and *why*. Link to the relevant
  Issue or Discussion if one exists.
- Commits are individually coherent and signed off. Squash fixup commits
  before requesting review.

One approval from a maintainer is required before merge. PRs are merged via
squash-merge into `main` to keep the linear history readable.

Branches are deleted after merge. Do not commit directly to `main`.

### Development Setup

Prerequisites: Python 3.11 or later, and
[uv](https://docs.astral.sh/uv/) for dependency management.

```
git clone https://github.com/Rajesh1213/OCP.git
cd OCP
curl -Ls https://astral.sh/uv/install.sh | sh   # install uv if not present
bash scripts/dev-install.sh                       # create .venv and sync all packages
source .venv/bin/activate
```

Useful commands:

```
# Run the full test suite
pytest packages/ocp-conformance/

# Run the OCP-0002 conformance pack against the reference server
bash scripts/run-conformance.sh

# Start the reference server (stdio transport)
ocp-server

# Lint
ruff check packages/

# Type check
mypy packages/ocp-server/ocp_server packages/ocp-client/ocp_client

# Run a specific conformance test file
pytest packages/ocp-conformance/ocp_conformance/suite/test_state.py -v
```

Set `OCP_EMBEDDER=hash` (the default) to run without any external model
dependencies. Set `OCP_WATCH=0` in tests to disable the file-system watcher
and speed up test teardown.

---

## Contributing to the Specification

Specification changes affect every OCP implementer, not just this repository.
They therefore follow a heavier process than code changes.

### Typos, Clarifications, and Non-Normative Edits

Corrections to grammar, spelling, formatting, or non-normative explanatory
text (notes, examples, rationale paragraphs) may be submitted directly as a
Pull Request with no prior discussion required. Apply the same DCO sign-off
and commit message conventions as for code.

### Normative Changes

A normative change is anything that adds, removes, or modifies a MUST, MUST
NOT, SHOULD, SHOULD NOT, or MAY statement; introduces a new tool, field, error
code, or event type; or changes scope resolution, invalidation, or event
delivery semantics.

Before submitting a normative PR:

1. Open a GitHub Discussion or Issue describing the problem you are solving,
   the proposed change, and any alternatives you considered. Label it
   `spec-change`.
2. Allow at least one week for community feedback before opening a PR. For
   changes with broad implementer impact, two weeks is preferred.
3. If the Discussion reaches rough consensus, open a PR. Reference the
   Discussion in the PR description.

Maintainers will assign an RFC identifier (e.g., `OCP-0003`) if the change is
accepted. The RFC number appears in the PR title and in the commit that merges
it.

**Any normative spec change must include a corresponding update to the
OCP-0002 conformance test pack in the same PR.** A specification change
without tests will not be merged. This is a hard rule, not a preference.

Normative PRs require approval from at least **two** maintainers before merge,
not one.

### Major Changes and New RFCs

If you are proposing a substantial new area of the protocol — a new
conformance level, a governance or security profile, a federated workspace
mechanism, or any other change large enough to warrant its own document —
propose it as a new OCP RFC file.

New RFC documents live under `spec/` and follow the structure of `OCP-0001`
and `OCP-0001a`: abstract, status, terminology, normative sections, open
questions, and a conformance checklist. Submit the new document as a PR. The
PR triggers the same Discussion-first process described above, but the review
period is typically longer (four weeks minimum for major RFCs).

RFC documents are written in Markdown. Tables of normative requirements must
use the RFC 2119 keywords (MUST, SHOULD, MAY). An implementer checklist
(like Appendix B of OCP-0001) is required for any RFC that introduces new
conformance requirements.

---

## Testing Requirements

### Unit Tests

New functionality in the reference server or client SDK must be accompanied by
unit or integration tests. Tests live under
`packages/ocp-conformance/ocp_conformance/suite/`. Name the test file after
the area it covers (`test_state.py`, `test_coordination.py`, etc.).

### Conformance Tests

The OCP-0002 conformance pack (under `packages/ocp-conformance/`) exercises
every normative MUST and SHOULD in the specification. This pack is the source
of truth for whether the reference server is compliant.

The following rules apply to every PR:

- **The conformance pack must pass without any test removed or skipped.**
  Do not use `@pytest.mark.skip` to make a PR green. Fix the root cause.
- **New normative spec requirements must come with new conformance tests in
  the same PR.** Add them to the appropriate suite file or create a new one.
  A spec change that lands without tests will be reverted.

To run the conformance pack:

```
bash scripts/run-conformance.sh
```

To run only a specific conformance level:

```
OCP_LEVEL=core bash scripts/run-conformance.sh
OCP_LEVEL=coordination bash scripts/run-conformance.sh
```

To run against a third-party server instead of the reference implementation:

```
OCP_SERVER_CMD=/path/to/your-ocp-server bash scripts/run-conformance.sh
```

### Interoperability Tests

Integration tests that verify client/server interoperability across different
OCP implementations are appreciated but not required for a PR to merge. If you
have access to an alternative OCP server implementation and can demonstrate
cross-implementation compatibility, include a note in the PR description.

---

## Coding Standards

The codebase is Python 3.11+ throughout.

- **Type annotations are required** on all public functions and methods.
  Run `mypy` before submitting; the CI type-check must pass.
- **Formatting and linting** are handled by `ruff`. Run `ruff check` and
  `ruff format` before committing. CI will reject PRs that fail either check.
- **Async throughout.** All I/O must be async. Do not introduce synchronous
  blocking calls in hot paths. Use `asyncio` primitives; the codebase uses
  `anyio` via the MCP SDK for subprocess and transport operations.
- **No global mutable state** outside of the `SQLiteStore` instance. Tool
  handler functions must be pure of side effects except through the store and
  the emit callback.
- **Error codes** returned to clients must use the OCP error vocabulary
  defined in §4.6. Never return raw Python exception text in a tool response.
- **Public APIs** (tool handlers, `OCPClient` methods, `BaseStore` abstract
  methods) must be documented with a short docstring. Inline comments should
  explain *why*, not *what*.
- File names use `snake_case.py`. Module layout follows the existing structure:
  storage layer in `ocp_server/storage/`, tool logic in `ocp_server/tools/`,
  server wiring in `ocp_server/server.py`.

---

## Communication

- **GitHub Discussions** — design questions, RFC proposals, general community
  conversation (link to be added once Discussions are enabled on the
  repository).
- **GitHub Issues** — bug reports, feature requests, spec questions.
- **Discord** — real-time discussion (link to be added).
- **Maintainer contact** — see `MAINTAINERS.md` (to be added). For security
  matters only, use the private contact method described in `SECURITY.md`.

Response time: maintainers aim to triage new issues and PRs within one week.
Complex PRs may take longer. If you have not received any response after two
weeks, it is appropriate to leave a comment on the issue or PR to request
attention.

---

## License

By contributing to this repository you agree that:

- **Code contributions** (files under `packages/`, `scripts/`, and any other
  non-specification files) are submitted under the
  [Apache License, Version 2.0](./LICENSE).
- **Specification contributions** (files under `spec/`) are submitted under
  the [Creative Commons Attribution 4.0 International License](./spec/LICENSE.md).

By signing off your commits with `git commit -s`, you affirm under the
[Developer Certificate of Origin](https://developercertificate.org/) that you
have the right to submit your contribution under these terms. You retain
copyright in your contributions; you are granting a license, not transferring
ownership.
