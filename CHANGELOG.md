# Changelog

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
