# Multi-interpretation search

The public `resize(source, target_canvas)` API is unchanged. Inference uses
offline geometry, typography, grouping, wording and asset-name features.
Softmax scores are heuristic probabilities, not calibrated probabilities.
Explicit groups retain their members. Proximity-only inferred text groups
use complete linkage; linguistic continuations can still form longer phrases.

The base model keeps the highest eligible role for each unit. Weak background
evidence cannot remove content from layout; ambiguous decoration remains a
foreground choice in the base model. A bounded beam explores up to three
credible roles per ambiguous unit. Alternative models allow at most one hero
and headline, select one primary logo, and recompute repeated sets, alignment
and importance. Expected importance averages the source role distribution;
layout importance combines 75% assigned-role importance with 25% expected
importance, retaining primary/secondary logo and sponsor conventions.

Each interpretation uses the existing templates and fitting. All candidate
attempts (including failed fits) share a global budget, divided fairly across
interpretations. Only the globally strongest candidates receive Z3 refinement.
The source reference and its expected importances stay fixed during ranking.
Common visual terms include readability, new overlap, source type hierarchy,
balance, drift, reading order and nearby alignment/overlap preservation.
A bounded role preference and a modest mean log-plausibility prior supplement
these terms. This avoids making excluded content disappear from scoring just
because an alternative calls it decoration.

Contain images remain uniformly scaled, including backgrounds. Cover boxes
may fill the target, as permitted by the existing schema/checker and renderer.
No assets, source pixels, opacity, z-order, text or font families are rewritten.
Every candidate and refinement is checked; existing defensive fallbacks remain.

## Configuration

Environment variables are read per resize call. Invalid values use defaults;
numeric values are clamped to bounded ranges.

| Variable | Default | Range / purpose |
|---|---:|---|
| CHAMELEON_INTERPRETATIONS | 4 | 1-8; maximum interpretations, not a quota |
| CHAMELEON_AMBIGUITY_MARGIN | 0.30 | 0-1; maximum top-two probability gap to branch |
| CHAMELEON_CANDIDATES | 40 | 1-128; total candidate attempts |
| CHAMELEON_REFINE_TOP | 3 | 0-8; global refinement limit |
| CHAMELEON_PRIOR_WEIGHT | 0.08 | 0-1; mean log-plausibility prior |
| CHAMELEON_ROLE_WEIGHT | 0.10 | 0-1; bounded role-dependent preference |
| CHAMELEON_RELATION_WEIGHT | 0.15 | 0-1; source relationship penalty |

`CHAMELEON_DEBUG=1` prints role distributions, interpretations, candidate
scores and the winner. `engine.reflow.last_stats` exposes search counts and
the winning assignment for evaluation; like the pre-existing `last_choice`,
this diagnostic state is intended for sequential runs, not concurrent calls.

## Repeatable evaluation

Run in Ubuntu/WSL with the environment activated:

```bash
python -m pytest -q
python -m tools.run_eval --render --out build/comparisons/after
python -m tools.compare_eval build/comparisons/baseline-replay build/comparisons/after --before-images build/comparisons/before/evaluation
```

Each run saves task/output JSON, per-task validation, runtime, interpretation
counts, candidate attempts/passes, refinement attempts and the winning role
assignment. Render failures are recorded in metrics.json. With --render,
unmutated cases get source/output PNGs and a contact sheet.

The comparison verifies identical task contents and reports validation,
warnings and timing. Open `build/comparisons/after/comparison.html` to inspect
source/before/after images. Scores from different scorer versions are not
directly comparable. For less biased judging, hide before/after labels and
record wins/ties/losses before revealing them.

The original committed engine can be replayed in an isolated checkout with
the same evaluation runner to recover per-task data that older reports did
not save. Keep original baseline artifacts unchanged. Run timing comparisons
sequentially on the same machine without other heavy work.

## Limits

This is bounded heuristic search, not image-content recognition or a learned
visual judge. Asset names can be misleading. The grouping hypotheses themselves
are fixed across interpretations. Spatial relationships are soft across units;
extreme aspect changes necessarily trade some alignment for readability.
Z3 retains its existing wall-clock timeout, so timeout-bound results may vary
with machine load even though inference, search ordering and tie-breaking are
deterministic. Invalid inputs and impossible tiny canvases retain the existing
best-effort fallback behavior. No new runtime dependencies are required.

## Local baseline comparison

The saved 277-task benchmark (three supplied designs, four synthetic branding
designs, catalogue targets and mutations) passed all 277 tasks both before and
after this change, with no crashes/timeouts or new validation failures.
Tiny-text warnings decreased from 443 to 433; new-text-collision warnings
decreased from 1 to 0. An alternative interpretation won on 16 tasks.
Mean engine runtime increased from approximately 140 ms in the user's original
baseline to 180 ms in the final rendered run. The final test suite passed
609 tests, including offline and deterministic-output examples.

All 87 unmutated target previews and their source images rendered successfully.
Representative visual inspection showed mixed, generally modest changes:
some headlines/CTAs become more prominent while supporting copy can get smaller.
Narrow banners remain difficult. Warning reductions are evidence of specific
improvements, not proof of superior visual quality overall. No Crello or held-out
real-design evaluation was performed. Use the comparison gallery for review.

Docker verification was unavailable because Docker Desktop's WSL integration
is not enabled in this distro. The runtime dependency files and Dockerfile are
unchanged; socket-disabled resizing tests pass.
