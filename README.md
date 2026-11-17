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

Student UI: English, Korean, Chinese, Vietnamese, and Russian. Administration, server messages, and documentation use English. The language selector remembers the student's choice.

Instructors can edit problem titles and statements in each language. Missing translations fall back to English. CSV contents, filenames, code, and GCC diagnostics remain unchanged.

## Scoring

- Correctness: passed tests / all tests × 60.
- All tests must pass to receive a rank and performance points.
- CPU: 30 × best time / own time. Memory: 10 × best Peak RSS / own Peak RSS.
- One checked warm-up and 10 measured runs per test by default. Time is the sum of per-test CPU medians; memory is the largest measured RSS.
- A native parent measures CPU/RSS outside the student process. Common data loading/output are included. Compilation and private answer validation are excluded.
- The latest completed submission per student is ranked. Test/environment profiles separate incompatible measurements. Administrators can rejudge older submissions.

## Samples and development

The provided originals in `data/sample_csv/` are preserved. Importing samples generates clearly marked demonstration answers using ascending UTF-8 `strcmp` on the first column, then all columns for ties:

```bash
docker compose exec web python scripts/import_samples.py
```

Use instructor-prepared answers for actual assignments. See [deploy/README.md](deploy/README.md) for storage, migration, isolation, and backups.

For host development only:

```bash
uv sync --frozen
.venv/bin/python scripts/serve.py
.venv/bin/python -m judge.worker      # requires host Docker access
.venv/bin/python -m pytest -q
node --check app/static/app.js
node --check app/static/i18n.js
.venv/bin/python scripts/verify_samples.py  # only the repository-authored reference
```

Tests cover registration-free submissions, private passwords and history, simultaneous first submissions, ID corrections, CSV validation, scoring, languages, Compose separation, and worker cancellation/recovery. Actual container execution requires Docker daemon access and built images.
