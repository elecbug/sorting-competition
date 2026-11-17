# Operations

## Docker Compose

```bash
python3 scripts/setup.py
docker compose up -d --build
```

Setup creates `.env` or adds missing Compose variables without replacing existing credentials. It detects `SORT_UID`, `SORT_GID`, and `SORT_DOCKER_GID` from the host. The application containers run with the configured UID/GID, matching the owner of existing database files. A custom data directory must be writable by that UID.

| Service | Purpose | Access |
| --- | --- | --- |
| `web` | HTTP UI/API on port 8100 | Persistent `/data`; no Docker socket |
| `worker` | One sequential judge queue | `/data`, Docker socket, private job volume |
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

## Storage and sandbox paths

`SORT_DATA_DIR` is the **host** directory bind-mounted as `/data`. The existing `competition.db`, samples, generated answers, sources in SQLite, and private answers stay there. Never mount this directory into student execution containers.

The separate named volume `SORT_JOB_VOLUME` is mounted at `/jobs` in the worker. For each job the worker writes a new unique directory containing only `student.c`. The student container mounts just that directory using `volume-subpath`, read-only, as `/input`. This avoids interpreting a worker-container path as a Docker-host path and prevents one submission from reading other submissions. Docker Engine must support volume subpaths.

Keep `SORT_UID`/`SORT_GID` consistent with the data owner. To run independent installations on one daemon, use separate Compose project names, image names, data paths, and `SORT_JOB_VOLUME` values.

`docker compose down` keeps persistent files and the job volume. Do not use `down --volumes` as part of routine updates.

## Student verification and ID correction

Students submit directly with an ID and a personal password (4–128 characters). The first accepted queue insertion creates only the verification record; invalid/closed submissions do not reserve an ID. Later submissions require that password even if a browser session already exists. Concurrent first submissions cannot overwrite each other's password. Passwords are salted scrypt hashes and are excluded from API responses and logs.

There is no registration, class code, nickname requirement, or student login step before submission. A 12-hour private session allows the student to see their own history afterward. From another browser, use the ID/password form on the Submissions tab. Unknown IDs are not created by history verification. The admin sign-in endpoint accepts only administrators.

To correct an ID, open **Students → Correct ID**, enter the new ID and the student's existing personal password. The password must match the record being corrected. The user ID and all submissions/results are preserved; the displayed student ID changes. Existing sessions for that student are revoked. Corrections are audited without storing passwords. A target ID already in use returns a conflict; records are not merged. The instructor resolves the actual identity when student IDs collide.

The former registration records/passwords remain usable. `SORT_ENROLLMENT_CODE` from old configurations is ignored and can be removed. Leaderboards, histories, and grade exports display student IDs.

## Languages and HTTPS

The UI supports `en`, `ko`, `zh`, `vi`, and `ru`, including administrative controls, verification, statuses, and ordinary errors. Dates/numbers follow the selected locale. Administrators may select any supported UI language; English is the source/fallback language. Problem title/description translations are edited via **Content language**, with English fallback. CSV data, filenames, C code, and GCC diagnostics are preserved.

Connect your domain and use `Caddyfile.example` for TLS. Set `SORT_HOST=127.0.0.1` (host-side published address) and `SORT_COOKIE_SECURE=true`, then recreate containers with `docker compose up -d`. The container itself always binds `0.0.0.0:8100`. Requests are limited using the peer address; add per-client limits at your proxy as needed. Arbitrary forwarded-IP headers are not trusted.

## Grading and isolation

One worker processes submissions on `SORT_CPU`. Temporary execution containers use a fixed image ID, no network/shared IPC, read-only root, restricted capabilities, default Docker seccomp, and bounded tmpfs. Neither the Docker socket nor database is exposed to student processes.

The trusted supervisor/native measurement parent use a separate UID from GCC and student code (65534). Private answers stay in supervisor memory, outside the candidate process. A small native launcher forks the measured child after replacing Python's address space, so large supervisor CSV allocations do not inflate candidate RSS. The native parent reports `wait4` CPU time (user + system) and Peak RSS through an FD closed in the candidate. Student-supplied timing output is ignored.

Defaults: 2 CPU seconds, 4 wall seconds, 128 MiB address space. The whole sandbox is limited to 1 GiB, including the supervisor. Candidate output, file descriptors, process count, and temporary storage are limited. Allocation failure followed by an invalid student pointer access may be reported as a runtime error.

Each test has checked warm-ups followed by N measured fresh processes. All runs must match the answer. CPU trimmed means are summed; the largest measured RSS is used. Compilation, Docker startup, Python CSV parsing, and answer verification are excluded. Shared C loading/output and input memory remain included.

CPU affinity does not reserve a physical core. Keep other services and its SMT sibling idle for fair measurements. Docker shares the host kernel; use a dedicated judge VM/server for hostile public workloads.

## CSV, profiles, and ranking

CSV files: UTF-8 with optional BOM, up to 32 MiB, 100,000 data rows, 64 columns, 2,000,000 total cells, and 16 KiB per cell. NUL is rejected. A problem supports up to 10 pairs and 128 MiB combined input/answer data.

Input and answer headers and complete-row multisets must match on upload, including duplicate counts. During grading, every cell and the complete row order must match the answer. Specify tie-breaking for equal keys. CSV quoting/BOM/record separator differences do not affect parsed values; whitespace/newlines inside cells remain data.

The image ID, CPU, limits, repetitions, and test digests define a measurement profile. Old results remain in history but are excluded from changed profiles. Use **Rejudge with current tests** after changing data or grading environments. Changing translated statements alone does not invalidate results.

The leaderboard and its CSV export show only students whose latest completed submission in the current profile passes all tests. A newer failed submission removes that student from the leaderboard until they pass again; queued/running submissions and judge errors do not replace completed results. Failed submissions remain available in submission history. Administrators are excluded. Correctness is passed/all × 60. Performance weights depend on the problem's scoring mode, as described below. Tolerance groups compare each measurement against the group's first member.

## Backups

The database contains private code, password hashes, and answer files. Use SQLite's backup API while running; do not copy only the main file while WAL is active. Back up `.env` with restricted permissions. Uploaded private data and generated answers must not be added to tracked sample directories.

## Current measurement policy

Run the configured warm-ups, then N measured fresh processes, sort the CPU samples, remove `floor(N × SORT_TRIM_FRACTION)` from each tail, and average the remainder. Peak RSS is the maximum across measured runs. Default warm-ups: 3; settling delay after preparation: 0.2 seconds; trim: 10%. N supports 3–100. The selected CPU also has a one-CPU quota. Keep the reserved core and its SMT sibling free of other workloads.

A profile includes sandbox image, boot ID, CPU, limits, repetitions, warm-ups, trimming, and settling. A host reboot or changed profile requires rejudging. Accepted measurements are canonical per stripped executable hash/profile, with source-hash aliases for exact repeats. Clearing student submissions resets these caches. Job tokens and monotonically increasing submission IDs prevent an interrupted old job from updating a later submission after deletion.

Scoring modes: balanced 60 correctness / 30 time / 10 memory; time-priority 60 / 40 / 0; memory-priority 60 / 0 / 40. All tests must pass for a rank. Focused modes rank their selected metric. Time tolerance is 1%; memory tolerance is 64 KiB for memory-priority assignments and 1 MiB otherwise. Results in the same tolerance group receive equal component points.

The judge image now installs libclang for source policy checking, so rebuild **all services including judge-image** after upgrading. Only explicit standard I/O and allocation APIs are allowed; the exact allowlist is in `judge/policy.py`. Syntax and object-symbol validation are complementary checks for the supported C subset, not a proof about arbitrary self-modifying machine code. Candidates cannot use inline assembly, assembler aliases, location spoofing, or unresolved indirect function calls.

## Applying the prepared course set

`data/pending_problem_set.json` refers to private generated input/answer files. At the next updated web startup, the application validates them, takes a SQLite backup, removes the listed old sample problems and their sample submissions, and creates three unpublished assignments. Original source CSVs, student identities, and unrelated problems are retained. Each manifest revision is applied once; subsequent revisions update the same managed problem and case IDs, preserving publication/submission settings and student submissions. Changed test data invalidates the relevant measurement profile. No live database replacement is performed while an older server image that does not enforce publication is running.

```bash
sudo docker compose up -d --build
```

Then sign in as administrator and enable **Published to students** when ready. Changing the checkbox on an existing problem takes effect immediately; it is separate from submission closure. An unpublished problem and its input/history/ranking routes return no student-visible content. Administrators can still inspect it.

All supported UI languages are now available while signed in as administrator; server logs and API messages continue to use English. **Clear student submissions** previews the count and requires confirmation, deletes only student code/results/queue entries, retains IDs/passwords and assignment data, and stops matching active jobs. The feature is not executed automatically by an update.
