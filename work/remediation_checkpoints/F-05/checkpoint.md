# F-05 pre-change checkpoint

- Created: 2026-08-30 Africa/Lagos
- Scope: `main.py`, `tests/test_infrastructure.py`, and a new opt-in PostgreSQL registration atomicity test module.
- Pre-change `main.py` SHA-256: `B51E493E124D1FA728D8BE606AD447B99EB8800BB8A3092AD615F787B47BE581`
- Pre-change `tests/test_infrastructure.py` SHA-256: `80B3B1E0D59D07AC3C12B277B8741D7F320C62AE99FD6946D89EB16BFCE631C1`
- Root cause: authorization consumption, business, admin and subscription were committed separately. A failure after any commit could not roll earlier durable state back.
- No schema change is planned; no Alembic revision is required.
