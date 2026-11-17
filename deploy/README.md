# Server operations

The web process and judge worker are separate. Only the worker needs Docker access.

```bash
uv sync --frozen
.venv/bin/python scripts/setup.py
.venv/bin/python scripts/import_samples.py  # optional generated demonstration problems
sudo bash deploy/install.sh
```

The installer builds `sorting-judge:local` and installs `sorting-arena-web` and `sorting-arena-worker` systemd services. It grants `SupplementaryGroups=docker` only to the worker service without changing the account's group membership. Default bind address: `0.0.0.0:8100`. Configure firewall and routing according to your server policy.

```bash
sudo systemctl status sorting-arena-web sorting-arena-worker
sudo journalctl -u sorting-arena-worker -f
sudo systemctl restart sorting-arena-web sorting-arena-worker
```

Read the administrator password and student registration code from `.env`. Sign in as `admin` to create problems, edit translated statements, and upload input/answer pairs. Input downloads can be public or private per test; answer downloads are never exposed. Restart both services after editing `.env`. Existing administrator sessions are revoked when the configured password changes.

## Languages

Administration and server output use English. The student UI offers `en`, `ko`, `zh`, `vi`, and `ru`, including submission statuses and ordinary error messages. Date and number formatting follows the selected language. The browser remembers the preference. Administrators always see English.

Use **Content language** in the problem editor for translated titles and descriptions. English is required; empty translations fall back to it. Changing a statement does not change CSV content or invalidate measured results. Raw C code, CSV values, original filenames, and GCC diagnostics retain their original content.

## HTTPS

Use `Caddyfile.example` after connecting your domain to the server. Set `SORT_HOST=127.0.0.1` and `SORT_COOKIE_SECURE=true` behind a TLS proxy, then restart services. Requests are limited using the actual peer address; add per-client limits at the proxy when appropriate. The application does not trust arbitrary forwarded IP headers.

## Isolation and measurement

- A single worker processes submissions sequentially on `SORT_CPU`. A file lock prevents duplicate workers against the same database.
- Containers use a fixed image ID, no network, no shared IPC, a read-only root filesystem, restricted capabilities, Docker's default seccomp, and bounded tmpfs storage. They never receive the Docker socket or database directory.
- The supervisor and native measurement parent retain a separate UID from GCC/student code. GCC and student executables run as UID/GID 65534. Private answers remain in the supervisor's memory and are not passed as files to the student process.
- Student processes have CPU, wall time, address-space, output-size, file-descriptor, and process-count limits. Defaults: 2 CPU seconds, 4 wall seconds, 128 MiB address space. The entire container, including the CSV supervisor, has a 1 GiB memory limit.
- The supervisor starts a native launcher; that launcher forks a fresh measured child after replacing Python's address space. This prevents the supervisor's large CSV heap from inflating student RSS. The native parent reports `wait4` CPU time (user + system) and Peak RSS through an FD closed in the candidate. Student-generated timing output is ignored.
- One checked warm-up precedes each test's measured repetitions. Every run is checked against the answer. CPU medians are summed across tests; the largest measured RSS is used. Compilation, Docker startup, Python CSV parsing, and answer comparison are excluded. Common C data loading and output remain included.
- Address-space exhaustion may appear as a runtime error if student code dereferences a failed allocation. Peak RSS includes input data and the common runtime.
- CPU affinity does not reserve a physical core. Keep other services and its SMT sibling idle for fair measurements. Docker shares the host kernel; for hostile public workloads, place the judge on a dedicated VM/server or use a stronger isolation runtime.

## CSV and answers

Files must be UTF-8 (BOM accepted), up to 32 MiB, 100,000 data rows, 64 columns, and 2,000,000 total cells including the header. Each cell is limited to 16 KiB; NUL is rejected. Each problem supports 10 input/answer pairs and 128 MiB combined data.

Upload validation requires identical headers and the same multiset of complete rows, including duplicate counts. During judging, **all cell contents and the complete row order must match the answer**. Define tie-breaking explicitly when equal keys have different rows. File quoting/BOM/record separators do not affect grading after CSV parsing; whitespace and newlines inside cells remain data.

The sample importer generates demonstration answers using the specified column, ascending UTF-8 `strcmp`, then all columns left to right for ties. It preserves originals and marks these problems as generated samples. Use instructor-prepared answers for actual assignments.

## Profiles and ranking

Image ID, CPU, resource limits, repetition count, and test digests define a profile. Old results remain in submission history but are excluded from changed profiles. Use **Rejudge with current tests** in submission details after changing data. Rebuild the image and rejudge after changes to the grading implementation or hardware.

Only the latest completed submission per student in the current profile is used. A newer wrong answer replaces a previous accepted result. Queued/running submissions and judge errors do not replace completed results. Administrator submissions are excluded.

Correctness is the fraction of passed tests × 60. All-passing entries receive relative performance points: `30 × best time / own time` and `10 × best RSS / own RSS`. Scores change when participant records improve. Score differences within 0.1, time within 1%, and RSS within 1 MiB share ranks in their respective leaderboards. Ties compare against the first member of a group, avoiding chained ties.

## Backups and updates

`data/competition.db` contains accounts, source code, and private answers. Back it up with SQLite's backup API while running, along with `.env`, using restricted permissions. Do not copy only the main database file while WAL is active.

For updates: `uv sync --frozen`, rebuild the judge image if required, then restart both services. Runtime files and generated answers are excluded from Git; keep actual private assignment data out of tracked sample directories.
