# Database & Storage Backup / Restore (Cauldra)

Independent, off-platform disaster-recovery backups of production Supabase
data, uploaded to a dedicated Cloudflare R2 bucket — the PostgreSQL database
(`scripts/backup_database.py`) and Supabase Storage's private `cauldra-private`
bucket (`scripts/backup_storage.py`). This is **in addition to** Supabase's
own point-in-time recovery / scheduled backups (see `DEPLOYMENT.md`) — not a
replacement for them.

## How it works

`scripts/backup_database.py`:

1. Reads the application's existing `DATABASE_URL` (the same variable
   `backend/main.py` already uses — no separate/duplicate DB URL variable
   was introduced) and the four existing R2 variables.
2. Runs `pg_dump --format=custom` (PostgreSQL's compressed, `pg_restore`-
   compatible archive format) into a temporary file.
3. Uploads that file to the `cauldra-backups` R2 bucket.
4. Verifies the object actually exists in R2 (`HEAD` request, size-checked)
   before declaring success.
5. Always deletes the temporary local file — on success **and** on failure.

Nothing is ever deleted from R2 by this script. Retention/lifecycle rules
will be configured separately, later, once manual backups are confirmed
working end-to-end.

## Running a backup manually

From the Railway service environment (or any environment with `DATABASE_URL`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, and
`R2_BACKUP_BUCKET` set, and the `pg_dump` client binary installed — see the
Dockerfile):

```sh
python scripts/backup_database.py
```

or locally on Windows, against a **non-production** database only:

```sh
venv/Scripts/python.exe scripts/backup_database.py
```

Exit code `0` means the backup was created, uploaded, **and verified**.
Any other outcome exits non-zero and logs a safe (secret-free) failure
message — see the script's own docstring for exactly what it does and does
not log.

This is not scheduled yet (deliberately — see the task this was built for).
Run it by hand for now; a cron/Railway-scheduled job will be added
separately once a real production backup has been confirmed to work.

## Where backups land

```
cauldra-backups/database/<YYYY>/<MM>/cauldra-db-<UTC timestamp>.dump
```

Example: `database/2026/09/cauldra-db-2026-09-05T10-30-00Z.dump`

Never at the bucket root — always under `database/<year>/<month>/`.

## Restoring a backup

**Never restore directly against the production database.** Always restore
into an isolated, disposable PostgreSQL instance first (a local Docker
Postgres, a scratch Supabase/Railway Postgres instance, or similar) —
whether you're verifying a backup is good, or investigating an incident.
`pg_restore` can drop/overwrite objects in whatever database you point it
at; pointing it at production by mistake is exactly the kind of accident
this whole backup system exists to protect you from.

### 1. Download the backup from R2

Using the AWS CLI configured against R2 (or any S3-compatible client/the
Cloudflare dashboard):

```sh
aws s3 cp \
  s3://cauldra-backups/database/2026/09/cauldra-db-2026-09-05T10-30-00Z.dump \
  ./cauldra-db-2026-09-05T10-30-00Z.dump \
  --endpoint-url "$R2_ENDPOINT"
```

(`aws configure` first, using the same `R2_ACCESS_KEY_ID` /
`R2_SECRET_ACCESS_KEY` as object-level credentials — no admin-level
Cloudflare access is needed to read a backup.)

### 2. Create an isolated PostgreSQL database to restore into

Never the production database. For example, a throwaway local Postgres via
Docker:

```sh
docker run --name cauldra-restore-test -e POSTGRES_PASSWORD=test -p 5433:5432 -d postgres:16
createdb -h localhost -p 5433 -U postgres cauldra_restore_check
```

### 3. Restore with `pg_restore`

```sh
pg_restore \
  --host=localhost --port=5433 --username=postgres \
  --dbname=cauldra_restore_check \
  --no-owner --no-privileges \
  --verbose \
  ./cauldra-db-2026-09-05T10-30-00Z.dump
```

`--no-owner --no-privileges` is usually what you want when restoring into an
environment whose roles don't exactly match production's — drop them if
you're restoring into an environment with identical roles and want an exact
match.

### 4. Verify

Spot-check row counts / a few known records against what you expect, then
confirm the app can point at `cauldra_restore_check` (via a temporary,
separate `DATABASE_URL` — never production's) and boot correctly, e.g.
`alembic current` should show the expected head revision.

### 5. Tear down

Drop the throwaway database/container once you're done. It only ever
existed to prove the backup restores cleanly — it is not meant to become a
long-lived environment.

## Supabase Storage backup

### How it works

`scripts/backup_storage.py`:

1. Reuses the application's EXISTING server-side Supabase credential
   (`SUPABASE_URL` + `SUPABASE_SECRET_KEY`, or the legacy
   `SUPABASE_SERVICE_ROLE_KEY` — see `backend/supabase_client.py`) — no new
   Supabase environment variable was introduced. That credential is already
   validated as service-role-capable, which is exactly what's needed to read
   every object in a private bucket regardless of Row Level Security.
2. Recursively walks every folder in the `cauldra-private` Supabase Storage
   bucket (or whatever `SUPABASE_STORAGE_BUCKET` is set to, if set),
   downloading each real object — folder placeholders are walked into, never
   downloaded.
3. Uploads each object to the SAME R2 bucket the database backups use
   (`R2_BACKUP_BUCKET`), under `storage/<supabase bucket>/<original object
   path>` — the exact original path and filename, never altered.
4. Verifies each upload with a `HEAD` request, comparing `ContentLength` to
   the downloaded object's byte length, before counting it as backed up.
5. Prints a summary (files discovered / uploaded / failed / total bytes) and
   exits non-zero if any object failed — one bad object never stops the rest
   of the run; if the bucket has no listing at all, it exits successfully
   and logs `No files found.`

Backup-only: nothing is ever deleted or overwritten in Supabase, and nothing
already in R2 is ever touched or deleted. Not scheduled yet, same as
`backup_database.py`.

### Running a Storage backup manually

```sh
python scripts/backup_storage.py
```

Requires `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`),
and the same four R2 variables `backup_database.py` uses. All of these are
already configured for this project — no new environment variable is
required.

### Where Storage backups land

```
cauldra-backups/storage/cauldra-private/<original object path>
```

Example: a Supabase Storage object at `invoices/2026/09/receipt-42.pdf` in
`cauldra-private` lands at `storage/cauldra-private/invoices/2026/09/receipt-42.pdf`
in R2.

### Restoring a Storage object

Download it with the same R2 pattern as a database backup:

```sh
aws s3 cp \
  s3://cauldra-backups/storage/cauldra-private/invoices/2026/09/receipt-42.pdf \
  ./receipt-42.pdf \
  --endpoint-url "$R2_ENDPOINT"
```

To put it back into Supabase Storage at its original path (via the Supabase
dashboard, or a service-role `client.storage.from_("cauldra-private").upload(...)`
call), always restore to the exact original path and verify the restored
object's contents before relying on it. As with database restores, never
experiment directly against the production bucket — if you need to validate
a restored object end-to-end, upload it to a disposable/test bucket first.

## Operational notes

- `DATABASE_URL` currently points at Supabase's **session-mode** pooler
  (port `5432`). `pg_dump` requires session-level connection features and
  does **not** work against Supabase's **transaction-mode** pooler (port
  `6543`) — if `DATABASE_URL` is ever changed to the transaction pooler,
  this backup script (and any other tool needing `pg_dump`/`pg_restore`)
  will need a separate, session-mode connection string instead.
- If `pg_dump` reports a server-version-mismatch error against Supabase,
  the Debian-provided `postgresql-client` package in the Dockerfile may be
  older than Supabase's Postgres version. Switch to the PGDG apt repository
  for a newer `pg_dump`/`pg_restore` build in that case.
- The backup credentials (`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`) are
  object-level R2 credentials only — no admin-level Cloudflare API access
  is required or used anywhere in this system.
- Database backups and Storage backups share the SAME R2 bucket
  (`R2_BACKUP_BUCKET`), separated only by key prefix (`database/...` vs
  `storage/...`) — there is only one R2 bucket to configure/monitor for both.
