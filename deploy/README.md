# Operations

## Docker Compose

```bash
python3 scripts/setup.py
docker compose up -d --build
```

Setup creates `.env` or adds missing Compose variables without replacing existing credentials. It detects `SORT_UID`, `SORT_GID`, and `SORT_DOCKER_GID` from the host. The application containers run with the configured UID/GID, matching the owner of existing database files. A custom data directory must be writable by that UID.

| Service | Purpose | Access |
| --- | --- | --- |
| `web` | HTTP UI/API on port 8100 | Persistent `/data` and prepared-case volume |
| `worker` | Pipelined correctness checks and dedicated benchmark cores | `/data`, Docker socket, private job volume and prepared-case volume |
| `judge-image` | Build and prepare the sandbox image; exits successfully | No host mounts |

The worker starts after web health and judge-image preparation succeed. Web health is independent of worker readiness so submissions can remain queued during judge outages. Services use `restart: unless-stopped`, read-only root filesystems, non-root application users, dropped capabilities, and no-new-privileges. Ensure the Docker daemon itself starts at boot.

Only the worker receives the Docker socket, with the detected socket GID as a supplementary group. It does not receive the administrator password. The web image has no Docker CLI. Web, worker, and judge-image join the `sorting-comp-net` bridge with automatically assigned addresses from `10.230.15.0/24`. The worker uses the Unix socket to control Docker. Student execution containers use `--network none`.

```bash
docker compose ps
docker compose logs -f web worker
docker compose up -d --build           # rebuild and recreate after updates
```

A forced shutdown can interrupt judging. The worker labels temporary sandboxes with a database-specific owner ID. On restart it removes only that owner's unfinished containers before requeuing interrupted jobs, preventing overlapping performance runs. Graceful shutdown cancels the active sandbox and returns its submission to the queue.

### Existing systemd deployment

```bash
sudo bash deploy/install.sh
```

The installer builds images before stopping `sorting-arena-web` and `sorting-arena-worker`. It starts Compose and waits for health. If startup fails, it removes the new stack and restores the legacy services that were active. On success it disables those legacy services. Data is never deleted or reinitialized.

Do not run host and Compose workers concurrently against the same database. The database file lock prevents accidental duplicate workers.

## Measuring classroom response time

Set `SORT_REPETITIONS=10` and `SORT_BENCHMARK_CPUS=auto` before the session on a host with five benchmark cores and a housekeeping core. Image or measurement profile changes can trigger background rejudges. Let those finish before using the class replay as a latency comparison.

```bash
docker compose exec -T web python scripts/latency_report.py
```

The report counts each student submission once, from entry to the submission handler to its first grading verdict. Time spent in the queue is included. A later rejudge preserves the timestamp. An infrastructure error counts as awaiting a grading verdict. The output shows completed and outstanding submissions reaching 10 seconds separately. Pass `--since 2026-11-19T09:00:00+09:00` to select a session. Tracking begins at the first updated startup. Browser polling and network time are additional.

## Storage and sandbox paths

`SORT_DATA_DIR` is the **host** directory bind-mounted as `/data`. The existing `competition.db`, samples, generated answers, sources in SQLite, and private answers stay there. Never mount this directory into student execution containers.

The private `SORT_JOB_VOLUME` at `/jobs` holds each submission's temporary source or executable. The web and worker share `SORT_PREPARED_VOLUME` at `/prepared`. Uploading a CSV pair prepares immutable input and answer files under `/prepared/prepared-v1/<digest>`. Web startup prepares any missing pairs for open, published problems before accepting requests. The worker can also regenerate missing pairs from SQLite.

Each sandbox mounts its own job directory and the required case directories read-only using Docker volume subpaths. The supervisor opens the prepared input separately for each run and supplies it as standard input. Directory and file permissions restrict the answers to the supervisor's group. The student process drops supplementary groups.

Keep `SORT_UID` and `SORT_GID` consistent with the data owner. When sharing a Docker daemon between independent installations, use separate Compose project names, image names, data paths, job volumes and prepared-case volumes. `docker compose down` retains the volumes.

## Student verification and ID correction

Students submit directly with an ID and a personal password (4–128 characters). The first accepted queue insertion creates only the verification record; invalid/closed submissions do not reserve an ID. Later submissions require that password even if a browser session already exists. Concurrent first submissions cannot overwrite each other's password. Passwords are salted scrypt hashes and are excluded from API responses and logs.

The shared `SORT_ACCESS_CODE` protects the home page, downloads, student API and admin sign-in. Setup generates an eight-character code. Share it with the class. A valid code sets a signed 12-hour HttpOnly cookie. Change the code in `.env` and recreate the web service to revoke old admission cookies. The public `/healthz` reports web liveness. A 12-hour private session allows the student to see their own history afterward. From another browser, use the ID/password form on the Submissions tab. Unknown IDs are not created by history verification. The admin sign-in endpoint accepts only administrators.

To correct an ID, open **Students → Correct ID**, enter the new ID and the student's existing personal password. The password must match the record being corrected. The user ID and all submissions/results are preserved; the displayed student ID changes. Existing sessions for that student are revoked. Corrections are audited without storing passwords. A target ID already in use returns a conflict; records are not merged. The instructor resolves the actual identity when student IDs collide.

The former registration records/passwords remain usable. `SORT_ENROLLMENT_CODE` from old configurations is ignored and can be removed. Leaderboards, histories, and grade exports display student IDs.

## Languages and HTTPS

The UI supports `en`, `ko`, `zh`, `vi`, and `ru`, including administrative controls, verification, statuses, and ordinary errors. Dates/numbers follow the selected locale. Administrators may select any supported UI language; English is the source/fallback language. Problem title/description translations are edited via **Content language**, with English fallback. CSV data, filenames, C code, and GCC diagnostics are preserved.

Connect your domain and use `Caddyfile.example` for TLS. Set `SORT_HOST=127.0.0.1` (host-side published address) and `SORT_COOKIE_SECURE=true`, then recreate containers with `docker compose up -d`. The container itself always binds `0.0.0.0:8100`. Requests are limited using the peer address; add per-client limits at your proxy as needed. Arbitrary forwarded-IP headers are not trusted.

## Grading and isolation

One worker coordinates compilation, correctness checking, and benchmarking. Web, queue management and prechecks use the reserved housekeeping CPUs when available. Temporary execution containers use a fixed image ID, no network/shared IPC, read-only root, restricted capabilities, default Docker seccomp, and bounded tmpfs. Neither the Docker socket nor database is exposed to student processes.

The trusted supervisor/native measurement parent use a separate UID from GCC and student code (65534). The supervisor compares candidate output with the private encoded answer. The candidate receives just its input on stdin. A small native launcher forks the measured child after replacing Python's address space, so large supervisor CSV allocations do not inflate candidate RSS. The native parent reports `wait4` CPU time (user + system) and Peak RSS through an FD closed in the candidate. Student-supplied timing output is ignored.

Defaults: 2 CPU seconds, 4 wall seconds, 128 MiB address space. Each sandbox has a 1 GiB memory limit including its supervisor. Budget for one sandbox per active measurement core plus the correctness sandbox and worker. Candidate output, file descriptors, process count, and temporary storage are limited. Allocation failure followed by an invalid student pointer access may be reported as a runtime error.

Each test has checked warm-ups followed by N measured fresh processes. All runs must match the answer. CPU trimmed means are summed; the largest measured RSS is used. Compilation, Docker startup, Python CSV parsing, and answer verification are excluded. Shared C loading/output and input memory remain included.

Keep other host workloads off the benchmark cores and their SMT siblings. Docker shares the host kernel; use a dedicated judge VM/server for hostile public workloads.

## CSV, profiles, and ranking

CSV files: UTF-8 with optional BOM, up to 32 MiB, 100,000 data rows, 64 columns, 2,000,000 total cells, and 16 KiB per cell. NUL is rejected. A problem supports up to 10 pairs and 128 MiB combined input/answer data.

Input and answer headers and complete-row multisets must match on upload, including duplicate counts. During grading, every cell and the complete row order must match the answer. Specify tie-breaking for equal keys. CSV quoting/BOM/record separator differences do not affect parsed values; whitespace/newlines inside cells remain data.

Changing the environment profile archives the previous result of the latest completed submission for each student and problem in `prior_measurements`. The worker requeues those submissions once at lower priority than new submissions. The leaderboard fills with results from the new profile as they finish. After editing test data, use **Rejudge with current tests**. Changing translated statements alone does not invalidate results.

The leaderboard and its CSV export show only students whose latest completed submission in the current profile passes all tests. A newer failed submission removes that student from the leaderboard until they pass again; queued/running submissions and judge errors do not replace completed results. Failed submissions remain available in submission history. Administrators are excluded. Correctness is passed/all × 60. Performance weights depend on the problem's scoring mode, as described below. Tolerance groups compare each measurement against the group's first member.

## Backups

The database contains private code, password hashes, and answer files. Use SQLite's backup API while running; do not copy only the main file while WAL is active. Back up `.env` with restricted permissions. Uploaded private data and generated answers must not be added to tracked sample directories.

## Current measurement policy

Set `SORT_REPETITIONS` independently of `SORT_BENCHMARK_CPUS`. The default `auto` selects up to five distinct physical cores, preferring `SORT_CPU` and reserving at least one physical core for the web service and compilation on multicore hosts. Explicit lists such as `2,4,6,8,10` refer to Linux CPU IDs. Check the server topology with `lscpu -e=CPU,CORE,SOCKET`. On a single physical core the worker serializes both stages.

The worker compiles and validates the library policy once. A separate sandbox checks every case once. Students can see compile errors, incorrect answers, or that their correct solution is waiting for a benchmark. Exact source and executable matches in the same profile reuse the canonical measurement. Preparation and benchmarking overlap across submissions. Only one submission occupies the benchmark cores at a time.

All replicas report ready before the worker releases them together. Each replica has one CPU of quota, settles for `SORT_SETTLE_SECONDS`, and performs `SORT_WARMUP_RUNS` warm-ups per case. The worker distributes N samples across the cores, assigning any remainder to the first cores in the configured order. It pools the raw samples for each case, removes `floor(N × SORT_TRIM_FRACTION)` observations from each tail, then sums the per-case means. Memory is the highest individual measured RSS. Image ID, boot ID, CPU selection, limits, repetitions, warm-ups, trim, settle time and testcase digests identify comparable results.

### Comparing core counts

During a maintenance window, stop the web service and worker so their CPU use cannot overlap the larger calibration pool. Run the calibration command with representative correct submissions from the same problem. Replace 101 and 102 with actual submission IDs. It reads SQLite in read-only mode and runs the production sandbox protocol. Each JSON line gives benchmark completions per minute, per-round CPU time, Peak RSS, CPU coefficient of variation, and score spread. Scores and ranks use the production scoring rules within the selected group. Select a core count using both throughput and score dispersion. The tool tries the requested counts available in the configured pool. The worker lock prevents a calibration run from overlapping a live judge.

```bash
docker compose stop web worker
docker compose run --rm --no-deps worker python scripts/calibrate.py --submission 101 102 --cores 1 2 5 10 --rounds 3
docker compose up -d web worker
```

Scoring modes: balanced 60 correctness / 30 time / 10 memory; time-priority 60 / 40 / 0; memory-priority 60 / 0 / 40. All tests must pass for a rank. Time-priority ranks CPU time. Memory-priority ranks Peak RSS first and CPU time second when memory is equal. Time and memory-priority tolerances are zero; other memory comparisons allow 1 MiB. Results in the same tolerance group receive equal component points.

The judge image now installs libclang for source policy checking, so rebuild **all services including judge-image** after upgrading. Only explicit standard I/O and allocation APIs are allowed; the exact allowlist is in `judge/policy.py`. Syntax and object-symbol validation are complementary checks for the supported C subset, not a proof about arbitrary self-modifying machine code. Candidates cannot use inline assembly, assembler aliases, location spoofing, or unresolved indirect function calls.

## Applying the prepared course set

`data/pending_problem_set.json` refers to private generated input/answer files. At the next updated web startup, the application validates them, takes a SQLite backup, removes the listed old sample problems and their sample submissions, and installs the prepared assignments with their specified publication status. Original source CSVs, student identities, and unrelated problems are retained. Each manifest revision is applied once; subsequent revisions update the same managed problem and case IDs, preserving publication/submission settings and student submissions. Changed test data invalidates the relevant measurement profile. No live database replacement is performed while an older server image that does not enforce publication is running.

```bash
sudo docker compose up -d --build
```

The current two-problem course set starts published with submissions closed. Administrators can change **Published to students** and **Accept submissions** independently. Later manifest revisions preserve those administrator choices. Changing the checkbox on an existing problem takes effect immediately; it is separate from submission closure. An unpublished problem and its input/history/ranking routes return no student-visible content. Administrators can still inspect it.

All supported UI languages are now available while signed in as administrator; server logs and API messages continue to use English. **Clear student submissions** previews the count and requires confirmation, deletes only student code/results/queue entries, retains IDs/passwords and assignment data, and stops matching active jobs. The feature is not executed automatically by an update.

## Course reset and student identifier display

`python3 scripts/prepare_course_pair.py --reset-students` prepares a two-problem replacement with a one-time reset token. Inspect and verify the prepared CSVs before running the same command with `--apply`. Applying backs up SQLite, removes the listed previous problems, clears all submissions and benchmark caches, removes student users/sessions and identity-correction records, and retains administrator users. The reset token prevents later manifest edits from clearing new students again. Source CSV files remain available.

Student and anonymous API views mask identifiers as the first two characters, stars, and the last two characters. Lengths up to four show stars only. Administrator views and exports contain full identifiers. Enter actual identifiers in verification forms; masked response values are never copied into submission inputs.
