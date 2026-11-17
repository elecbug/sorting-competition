# Sorting Competition

A self-hosted CSV sorting judge. Instructors upload input/answer CSV pairs. Students submit a C function with their **student ID and a personal password**, without registration or a class code.

```c
#include "csv_sort.h"

void student_sort(rows *table) {
    /* Sort table->rows according to the problem. */
}
```

The judge supplies CSV parsing and checks header, cell contents, and complete row order against the answer. Sort columns, numeric/text rules, and tie-breaking belong in the problem statement.

## Deploy with Docker Compose

Requires Python 3 for initial setup, Docker Engine with volume-subpath support, and Docker Compose. The worker image includes Docker 29 CLI. No Python packages need to be installed on the host for deployment.

```bash
python3 scripts/setup.py
docker compose up -d --build
```

Open `http://SERVER:8100`. Administrator ID: `admin`; read `SORT_ADMIN_PASSWORD` from `.env`. Setup detects host UID/GID and the Docker socket group, and preserves existing credentials.

The stack has separate **web** and **worker** containers, plus a one-shot **judge-image** service that prepares the sandbox image. Each submission runs in another restricted, temporary container. Only the worker receives the Docker socket; the web container cannot access it. SQLite and uploaded CSVs remain in the host data directory.

Compose services join `sorting-comp-net` and receive addresses from `10.230.15.0/24`. Student execution containers run with networking disabled.

To migrate this server from the earlier systemd services:

```bash
sudo bash deploy/install.sh
```

This builds images first, stops the old services, and waits for the Compose services to become healthy. It restores previously running services if migration startup fails.

```bash
docker compose ps
docker compose logs -f web worker
docker compose up -d --build          # apply code/configuration changes
docker compose down                  # keeps the database and uploaded data
```

## Student submissions

1. Select a problem and enter the student ID, a personal password of at least four characters, and C code.
2. The first successful submission saves a password hash for that ID. Later submissions must use the same password.
3. Submission history is available after submitting, or by entering the same ID/password on the Submissions tab. No separate registration or student sign-in is required.

Passwords are never displayed to instructors or stored in plaintext. If an ID was mistyped, the instructor uses **Students → Correct ID**, verifies the student's password, and enters the correct ID. Submission ownership and results are preserved. An ID already used by another student cannot be silently overwritten or merged. Corrections are logged and previous student sessions are invalidated.

Existing student records and passwords continue to work. The old registration endpoint and class-code requirement have been removed. Leaderboards and CSV exports identify students by ID.

## Languages

Student UI: English, Korean, Chinese, Vietnamese, and Russian. English is the source/fallback language; administrators can also change the UI language. Server messages and documentation use English. The language selector remembers the student's choice.

Instructors can edit problem titles and statements in each language. Missing translations fall back to English. CSV contents, filenames, code, and GCC diagnostics remain unchanged.

## Scoring

- Correctness: passed tests / all tests × 60.
- All tests must pass to receive a rank and performance points.
- Balanced problems award up to 30 for CPU time and 10 for memory. Time-priority problems award 40 for CPU time; memory-priority problems award 40 for memory. Each component uses its weight × best measurement / own measurement, with the tolerance grouping described below.
- Three checked warm-ups and N measured runs per test. CPU uses the sum of per-test trimmed means; memory is the largest measured RSS.
- A native parent measures CPU/RSS outside the student process. Common data loading/output are included. Compilation and private answer validation are excluded.
- Only students whose latest completed submission passes all tests appear in the leaderboard and its CSV export. Failed submissions remain in submission history. Test/environment profiles separate incompatible measurements. Administrators can rejudge older submissions.

## Course data and development

The provided originals in `data/sample_csv/` are preserved. Prepare the three course assignments and apply them on the next updated web startup:

```bash
python3 scripts/prepare_course_problems.py
docker compose up -d --build
```

Each assignment has a public input, a larger private input with withheld rows, and a smaller private case. Answers remain private. Use input/answer CSV pairs for additional instructor-created assignments. See [deploy/README.md](deploy/README.md) for storage, migration, isolation, and backups.

For host development only:

```bash
uv sync --frozen
.venv/bin/python scripts/serve.py
.venv/bin/python -m judge.worker      # requires host Docker access
.venv/bin/python -m pytest -q
node --check app/static/app.js
node --check app/static/i18n.js
.venv/bin/python scripts/verify_course_problems.py  # only repository-authored references
```

Tests cover registration-free submissions, private passwords and history, simultaneous first submissions, ID corrections, CSV validation, scoring, languages, Compose separation, and worker cancellation/recovery. Actual container execution requires Docker daemon access and built images.

## Prepared assignments and publication

Run `python3 scripts/prepare_course_problems.py` on the host (or the same script inside the web container) to prepare three assignments from `data/sample_csv`. On the next web startup, the validated pending set replaces only the previous `[Sample]` problems. The database is backed up first under `data/backups`; original CSV files and student credentials are preserved. Each manifest revision runs once, atomically. Later revisions update the same managed problems while preserving publication status and submissions. New assignments start unpublished.

The assignments cover district records with an input-order requirement (column 2, memory priority), facility indexing (column 1, time priority), and equipment exports (column 2, time priority). Statements specify the required output and grading objective without naming an algorithm or spelling out optimization clues. Instructors can inspect private generation metadata; students see only published problem content and public inputs.

Administrators can change the UI language, toggle **Published to students** independently from **Accept submissions**, and clear student submissions with a confirmation showing the affected count. Clearing keeps student IDs/passwords, problems, and CSVs; it also clears benchmark caches and cancels active student jobs safely. Unpublished problems are unavailable through student lists, direct URLs, downloads, rankings, or submission history.

## Library policy and repeatability

Only the allowed standard I/O and allocation functions may be referenced. Comparisons, copies, and sorting must be implemented by students. Type/constant headers remain available. Checks use GCC's actual preprocessed source, a Clang syntax tree, and the object's unresolved symbols. Macro aliases, builtins, inline assembly, runtime-symbol replacement, and unresolved indirect calls are rejected. Compiler-generated structure-copy and stack-protection helpers are distinguished from explicit library calls. See `judge/policy.py` for the exact allowed API list.

CPU measurements use warm-up runs followed by N independent processes, discard the configured fraction at both ends, and average the retained observations. With N=100 and a 10% trim, 80 observations remain. `SORT_WARMUP_RUNS`, `SORT_TRIM_FRACTION`, and `SORT_SETTLE_SECONDS` configure preparation; repetitions support 3–100. The sandbox is pinned to one CPU with a one-CPU quota, fixed memory/swap limits, and sequential execution.

The same stripped executable and test/environment profile share one canonical accepted measurement. Exact-source repeats can reuse it before compilation. Comments and formatting that compile to the same executable therefore receive the same measurements. Close results share performance points as well as rank. Memory-priority problems use a 64 KiB tolerance; other memory rankings use 1 MiB, and time uses 1%. These measures reduce variation; CPU affinity alone does not eliminate interference from other host workloads.

Wrong-answer reports describe only invalid output, changed rows, header changes, or incorrect row/tie order. They do not reveal private expected values or mismatch locations.
