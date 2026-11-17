# Sorting Competition

A self-hosted CSV sorting judge for classroom assignments. Instructors upload an **input CSV and an answer CSV** for each test. Students submit a C function using the supplied structures:

```c
#include "csv_sort.h"

void student_sort(rows *table) {
    /* Sort table->rows according to the problem. */
}
```

The judge supplies CSV parsing, runs submissions in restricted Docker containers, and compares the resulting header, cell contents, and row order with the instructor's answer. Sort columns, numeric/text comparison, multiple keys, and tie-breaking are defined by the problem statement and answer file.

## Run

Requires Linux, Python 3.11+, uv, and Docker Engine.

```bash
uv sync --frozen
.venv/bin/python scripts/setup.py

# Optional: import the provided originals and generate demonstration answers.
# Default rule: first-column strcmp order, then all columns for ties.
.venv/bin/python scripts/import_samples.py

# Build the judge image and install services with startup on boot.
sudo bash deploy/install.sh
```

Open `http://SERVER:8100`. Administrator ID: `admin`. Read the password and class code from `.env` (mode 600, excluded from Git). Restart services after changing configuration.

For development:

```bash
.venv/bin/python scripts/serve.py
.venv/bin/python -m judge.worker
```

The worker needs Docker access. If unavailable, the website remains available and submissions stay queued. Student code never falls back to host execution.

## Interface and languages

The student interface supports **English, Korean, Chinese, Vietnamese, and Russian**. The language selector remembers the choice in the browser; initial selection follows the browser language, with English as fallback. Administration, server messages, logs, and documentation use English.

Instructors can edit each problem's title and statement for all five languages using the **Content language** selector. Missing translations fall back to the English statement. CSV contents, filenames, submitted code, and compiler diagnostics are preserved.

The interface contains problem selection, input downloads, code submission, results, and leaderboards. No generated scores or fictional participants are displayed.

## Scoring and execution

- Correctness: passed tests / all tests × 60.
- All tests must pass to receive performance scores and a rank.
- Time: 30 × best CPU time / student CPU time.
- Memory: 10 × best Peak RSS / student Peak RSS.
- Each test runs once for warm-up and 10 measured times by default. Time is the sum of per-test medians; memory is the highest measured RSS.
- A native parent measures CPU and RSS outside the student process. Common loading and output code are included; compilation and answer comparison are excluded.
- Latest completed submission per student, separate time/memory ranks, shared ranks for close results, and CSV export.
- Dataset and environment fingerprints separate incompatible results. Instructors can rejudge previous submissions.

## Files

| Path | Purpose |
| --- | --- |
| `app/` | FastAPI, SQLite, authentication, administration, multilingual UI |
| `app/static/i18n.js` | Five-language student message catalog; English source strings |
| `judge/worker.py` | Single sequential queue and Docker execution |
| `judge/supervisor.py` | Compilation, repeated execution, private answer validation |
| `judge/launch.c` | Native measurement and privilege separation |
| `judge/harness.c` | Build the provided C structures, call the function, serialize output |
| `examples/` | Public header, submission template, repository-authored reference |
| `data/sample_csv/` | User-provided original sample files; preserved |
| `data/generated_answers/` | Generated demonstration answers; excluded from Git |
| `deploy/` | Service installer, TLS proxy example, operations guide |

## Verify

```bash
.venv/bin/python -m pytest -q
node --check app/static/app.js
node --check app/static/i18n.js

# Run only the repository-authored reference against the full sample files.
# Never executes student submissions or creates leaderboard results.
.venv/bin/python scripts/verify_samples.py
```

Tests cover CSV parsing, exact answers, access restrictions, rate limits, profile separation, scoring/ties, translations, and compilation/measurement of trusted C fixtures. Docker execution requires Docker access and the judge image. See [deploy/README.md](deploy/README.md) for deployment, isolation, limits, and backups.
