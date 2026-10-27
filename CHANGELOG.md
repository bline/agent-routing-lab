# Changelog

## 0.2.1 (2026-10-27)

- **Required fields are validated explicitly.** A field with an unrecognised
  status no longer escapes validation, and a missing required field is an error
  from `compute` and `validate` instead of a `KeyError`.
- **Declaring both a git and a manifest snapshot is rejected.** `compute`
  previously used git silently.
- **`context_burden.complete` is false when any read-set item is unresolved or
  has no path,** and the new `unresolved_read_items` field counts them.
- **`manifest` skips symlinked files** (listed under `skipped_symlinks`), its own
  output folder, and folders holding an earlier `baseline-manifest.json`.

## 0.2.0 (2026-10-27)

**The task-revision definition changed**, so revision IDs differ from 0.1.0 for
the same inputs.

- **Task revision** now hashes schema, task ID, stage, target role, source
  document hashes and snapshot identity. Before, it hashed only the source
  documents, so these collided:
  - the same packet at different commits;
  - builder and reviewer profiles of the same packet.
  
  It is also defined when no source documents are listed.
- **`task_id`** must match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
- **`validate` type-checks every declared value.** Before, a string where a list
  belongs was accepted and counted by characters, and malformed commits and hashes
  were accepted.
- **The sidecar template** uses example values that pass production validation.

## 0.1.0 (2026-10-27)

First release: two-stage task profiles (`adapt`, `compute`, `validate`,
`manifest`), the design and shadow-mode protocol documents, and the sidecar
template.
