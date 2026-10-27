# Task profile design (v0)

A task profile describes a unit of work before a role is chosen to do it: before
a supervisor is picked, or before a builder or reviewer is. It exists so that a
later selection policy can be trained and evaluated on information that was
genuinely available at decision time.

## Requirements

A feature can be used to choose a configuration for a role only if it is:

1. **Reproducible.** The same inputs give the same value, byte for byte.
2. **Available before the decision.** It exists before the role is selected, and
   it does not include anything the selected role later produced.
3. **Separate from the treatment.** It describes the task, not the model, effort
   or harness assigned to it.

Determinism does not mean objectivity. A count over a list that someone declared
is reproducible, but it depends on what the declarer chose to list. The profile
labels each feature by kind so that this difference stays visible.

## Stages

| Stage | Recorded before | May declare | Used to choose |
|---|---|---|---|
| A | the supervisor is chosen | identity and the objective only | the supervisor |
| B | the builder or reviewer is chosen | everything in the schema | the builder or reviewer |

Stage B declarations usually come from the plan the supervisor wrote, so they
carry that supervisor's influence. That is acceptable for choosing the builder,
because they exist before the builder is chosen. It is not acceptable for
choosing the supervisor. The validator rejects a stage A profile that declares
plan content.

## Fields

Every declaration is a field:

```json
{"status": "declared" | "none" | "not_applicable" | "unknown", "value": ..., "basis": "..."}
```

- `value` is present only when `status` is `declared`.
- `none` means explicitly nothing; `unknown` means not established.
- `basis` says where the value came from.

A count over an unknown field is `null`, never 0. Every unknown declaration must
have a matching entry in `coverage`, and the validator enforces this.

Declared groups (stage B): `objective`, `read_set`, `expected_write_set`,
`interfaces`, `exclusions`, `verification`, `uncertainties`, `dependencies`, and
`obligations.{acceptance, state, persistence, recovery, compatibility}`.

## Input snapshot

A profile identifies the bytes the task starts from in one of two ways:

- **git:** a commit, resolved with `git ls-tree` in the repository passed to
  `compute`.
- **manifest:** a hashed list of files with verified copies, for working trees
  with uncommitted changes. `taskprofile manifest` writes one. Its hash depends
  only on the captured bytes.

A repository path without a commit is recorded as unknown. `compute` never reads
the current working tree as snapshot data. Authority documents (contracts,
proposals, clarifications) are listed with sha256 and verified when the profile
is computed.

## Treatment is not a feature

Assigned models and efforts that appear in a packet are listed under
`provenance.ignored_treatment_fields` and never enter the profile. The
assignment is a separate record, joined to the profile by `task_revision` (see
[shadow-mode protocol](shadow-mode-protocol.md)).

## Computed features

| Group | Kind | Definition |
|---|---|---|
| `snapshot` | computed | kind (git, manifest, none), files, bytes, dirty paths, copies verified (manifest) |
| `authority` | computed | documents declared, verified, mismatched, unavailable; bytes of verified documents |
| `read_set` | declaration-dependent | items declared, items without a path, files and bytes resolved at the snapshot, unresolved items |
| `write_set` | declaration-dependent | items declared, crates named (`rust/crates/<name>`), cross-crate flag, existing files matched, unmatched items |
| `obligations` | declaration-dependent | count per kind; total over the declared kinds; the kinds declared |
| `verification` | declaration-dependent | count, count per kind, executable checks, judgment checks |
| `lists` | declaration-dependent | counts of exclusions, interfaces, uncertainties, dependencies |
| `context_burden` | computed | known bytes of packet plus authority documents plus resolved read set; whether all components are known |
| `packet` | text | bytes and words of the source documents |
| `mentions` | text mention | crates, repository paths and commits mentioned in the packet text (weak, kept separate) |
| `task_revision` | computed | `<task_id>@<16 hex>`: hash of the source documents' sha256 values |

Verification kinds: build, test, lint, format, fixture-oracle (executable);
independent-review, manual (judgment); other.

## Adapters

New work should be declared directly as a sidecar (`docs/sidecar-template.json`).
Adapters convert historical packets that already carry some structure:

| Adapter | Format |
|---|---|
| `work-engine-contract-v1` | a contract with scope, acceptance and exclusions lists, plus an optional builder packet with a read set and gates |
| `work-engine-shared-contract` | a contract with a base commit, hashed authority documents, and disallowed and deferred lists |
| `markdown` | any Markdown packet: the document is recorded, every declaration is unknown |

## Known limitations (v0)

- Read-set bytes count whole files, so they are an upper bound when a packet asks
  for parts of files.
- `snapshot.bytes` is the whole repository at the commit, not a task measure.
- Verification kinds come from command names. Checks stated only in prose are not
  classified.
- Work type and authority state are never inferred from prose.
- Not implemented yet:
  - interface and symbol-reference measures (they need a code graph);
  - inter-declarer agreement audits;
  - a published JSON Schema document. The validator in code is normative for v0.
