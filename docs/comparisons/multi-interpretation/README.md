# Multi-interpretation layout comparison

This folder is a portable snapshot of the original engine and the
multi-interpretation engine on the same supplied and synthetic designs.
The gallery contains 87 source/before/after comparisons.

After cloning or downloading this branch, open [comparison.html](comparison.html)
in your browser. All images are included locally; no server or network is needed.
GitHub normally displays HTML source rather than running this gallery inline.

From the repository root in Ubuntu/WSL:

```bash
explorer.exe "$(wslpath -w "$PWD/docs/comparisons/multi-interpretation/comparison.html")"
```

See [comparison.md](comparison.md) for benchmark differences,
[report.md](report.md) for final validation, and [tests.txt](tests.txt) for
the test result. Search design and configuration are documented in
[../../../EVALUATION.md](../../../EVALUATION.md).

This is a saved review artifact, not an automatically refreshed benchmark.
To evaluate subsequent changes, follow EVALUATION.md. Identical images are
stored once under content-derived filenames to keep this snapshot smaller.
