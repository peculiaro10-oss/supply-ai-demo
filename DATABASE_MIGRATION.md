# SQLite to PostgreSQL migration

This procedure copies data without modifying or deleting the original SQLite
database. Run it during a maintenance window; stop all Cauldra writers first.

1. Back up the SQLite file and uploads directory, then checksum the copies.
2. Create an empty PostgreSQL database and a least-privilege application role.
3. Set `DATABASE_URL` to the PostgreSQL SQLAlchemy URL and run:

   ```sh
   alembic upgrade head
   python scripts/migrate_sqlite_to_postgres.py --sqlite-path /secure/backup/supply_ai.db --database-url "$DATABASE_URL"
   ```

4. The copy tool refuses a non-empty target, preserves source IDs and timestamps,
   copies tables in foreign-key order, compares every copied table's row count,
   and resets PostgreSQL integer sequences.
5. Compare key relationship counts independently: users per business, products
   per business, sales per business/product, subscriptions per business, and
   retained-upload metadata per business. Verify sample login, inventory, sales,
   purchase order, subscription, AI-usage, and private-download flows against a
   staging deployment.
6. Keep the original SQLite file read-only and retain it with backups until the
   PostgreSQL deployment and verification are accepted. To roll back before
   cutover, point the application back to the untouched SQLite backup in a local
   development/staging environment; do not point production at SQLite.

PostgreSQL-specific notes: SQLite's `PRAGMA foreign_keys` is not used; PostgreSQL
enforces the model foreign keys directly. Current integer primary keys and UTC
timestamps are copied as stored. Review any malformed legacy rows that violate
PostgreSQL constraints before retrying; the target transaction rolls back on a
copy failure.

## Backups and recovery

Use provider-managed point-in-time recovery where available, plus daily logical
`pg_dump` backups retained for at least 35 days and monthly backups retained for
12 months. Store encrypted backups in a separate account/region with access
controls. Test a restore at least monthly:

```sh
pg_restore --clean --if-exists --no-owner -d cauldra_restore cauldra-YYYYMMDD.dump
DATABASE_URL=postgresql+psycopg://... alembic current
```

After restore, verify row counts, foreign-key integrity, application health, a
representative authenticated request, and private-upload availability. Record
restore duration and recovery-point age. Backup success alone is not recovery
verification.
