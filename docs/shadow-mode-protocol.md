# Shadow-mode protocol for collecting task profiles

How to record profiles during ordinary work without changing that work. This is
phase 0 of a dogfooding approach: observations come from real dispatches, not
from a separate benchmark.

## Rules

| The profile step does | The profile step does not |
|---|---|
| Record a stage B profile before the builder or reviewer is chosen | Enter the builder's or reviewer's prompt, packet or context |
| Record who declared it: role, model, effort, harness, guidance hash | Change the plan, its requirements, the model, the effort, the review route or acceptance |
| Record validation failures and unknown fields | Block or delay a dispatch because a field is unknown or validation failed |
| Run `taskprofile` (no model call of its own) | Add a model call. The coordinator writes the declaration in its own turn |

## Sequence at each dispatch

1. **Freeze the plan.** The packet and its authoritative documents are final.
2. **Declare.** The coordinator writes the stage B sidecar from the frozen packet,
   naming no builder, reviewer, model or effort.
3. **Compute and freeze.** Run `taskprofile compute`, then
   `taskprofile validate --production`. Write the profile once. On failure, write
   a failure record and continue.
4. **Select.** Choose the builder or reviewer exactly as before.
5. **Record the assignment.**

Shadow mode enforces ordering, not blindness. If the same coordinator declares
the profile and chooses the builder by hand, it may already have a builder in
mind. Blind assignment needs a separate assignment step. A profile written after
its assignment is kept, but marked ineligible for unbiased analysis.

## Records

There are three records per task revision, each written once:

```
task-profiles/<task_revision>/
  sidecar-stageB.json     the declaration
  profile-stageB.json     compute output (or profile-failed.json with errors)
  assignment.json         task_revision, profile sha256, frozen-before-assignment flag,
                          role, requested and observed model/effort/harness, time
  overhead.json           declaration start and freeze times, coordinator call ids,
                          validation errors, unknown-field count
  SHA256SUMS
```

Timestamps live in `overhead.json`, never in the profile, so the profile stays
reproducible. Outcomes (acceptance, repairs, findings, cost per role) come from
the host project's existing records, joined by run and `task_revision`.

## Environment changes

Changing the coordinator model changes who writes declarations, so treat it as a
measurement-environment change. Introducing the profile step is one too: asking
for declarations may itself change how plans are written. Compare records only
within one environment, or include the environment as a variable.

## Pilot

Run the protocol on the next 10 eligible dispatches, then review:

| Criterion | Check |
|---|---|
| Every eligible dispatch has a profile record | 10 of 10 have a profile or a failure record |
| Profiles were frozen before assignment | flag true, or a reason recorded |
| Profiling changed nothing | No packet or prompt contains the sidecar or profile; assignments follow the existing selection rules |
| Snapshots verify | Re-running `compute` later gives identical bytes |
| Declarations vary | Per field: how often it is declared, and how many distinct values occur |
| Overhead measured | Declaration time, coordinator tokens and cost per profile, validation failure rate |

## Out of scope for phase 0

Adaptive or randomized assignment, a separate profiler call, trained routers,
and publication of observations.
