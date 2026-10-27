# agent-routing-lab

Open-source framework for deterministic task profiling, agent experiments, and
model routing.

**Status: v0.1.0, pre-pilot.** The task-profile tool and its protocol are ready
for a shadow-mode pilot. There is no trained router here, and no claim yet that
task features improve model selection.

## Why

Choosing which model, effort and harness should handle a coding task needs
measurements that are reproducible, available before the decision, and separate
from the decision itself. Public per-task results for current models rarely come
with reuse rights, and running a benchmark large enough to train a selector is
expensive. This project builds the measurement side first, so that observations
can be collected from real engineering work (dogfooding) and evaluated honestly
later.

## What's here

| Path | Contents |
|---|---|
| `src/agent_routing_lab/taskprofile.py` | Two-stage task profiles: `adapt`, `compute`, `validate`, `manifest`. Standard library only. |
| `docs/design.md` | Stages, field states, snapshot identity, feature kinds and definitions, limitations |
| `docs/shadow-mode-protocol.md` | How to record profiles during ordinary work without changing it, plus a 10-dispatch pilot |
| `docs/sidecar-template.json` | The stage B declaration a coordinator fills in |
| `tests/` | Synthetic, self-contained tests (`python -m unittest discover -s tests`) |

## Quick start

```sh
pip install -e .                      # or run: python -m agent_routing_lab.taskprofile
taskprofile compute sidecar.json --repo /path/to/repo -o profile.json
taskprofile validate profile.json --production
taskprofile manifest --root /path/to/repo --path 'src/**' --out-dir snapshot/
```

## Key ideas

- **Two stages.** Stage A is recorded before the supervisor is chosen and holds
  only identity and the objective. Stage B is recorded before the builder or
  reviewer is chosen. A feature is used only to choose a role that acts after it
  exists.
- **Four field states:** declared, none, not applicable, unknown. Unknown is never
  zero, and every unknown is listed as a coverage gap.
- **Treatment is not a feature.** Assigned models and efforts are excluded from
  the profile and recorded separately.
- **Feature kinds are labelled:** computed (from the snapshot and hashed files),
  declaration-dependent (deterministic given what was declared), and text
  mentions (weak).

## Roadmap

1. Shadow-mode pilot: profiles only, with no change to assignments.
2. Per-role configuration selection with logged assignment probabilities, so
   alternative policies can be evaluated from the same records.
3. Test whether profile features improve selection on held-out tasks, and add
   them to the selector only if they do.

First consumer: [Work Engine](https://github.com/bline/work-engine).

## Related work

- Ge et al., *Agent psychometrics: Task-level performance prediction in agentic
  coding benchmarks*, arXiv:2604.00594 (2026). Item response theory with task
  features; separates model and scaffold ability.
- Zhou et al., *Agent-as-a-Router: Agentic model routing for coding tasks*,
  arXiv:2606.22902 (2026). CodeRouterBench and execution-feedback routing.
- Manoharan et al., *Task-to-model optimization for enterprise LLM coding
  assistants*, arXiv:2608.08528 (2026). Expected cost per completed task with
  escalation.

## License

Apache-2.0. See [LICENSE](LICENSE).
