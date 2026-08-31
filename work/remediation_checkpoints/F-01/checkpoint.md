# F-01 pre-change checkpoint

- Created: 2026-08-30 Africa/Lagos
- Scope: `.env.example` and `tests/test_infrastructure.py`
- Pre-change SHA-256: `E6B2422DA27D27D8516C0D705D2BF0B34B6902F554E902DD8534AC1F5209F752`
- Pre-change `tests/test_infrastructure.py` SHA-256: `2418F85DC69F6D40BE77A73F6CFA42AF2B4B50719B892AE94EE3E21D3030879A`
- Sensitive line: line 17 contained an embedded database credential and exactly matched the active `.env` database URL.
- Secret retention policy: the credential value is intentionally not copied into this checkpoint. The active uncommitted `.env` remains the only local source needed by the operator until external rotation is completed.
- Pre-change repository match count, excluding backup/archive trees: 2 (`.env` and `.env.example`).

This hash-and-metadata checkpoint avoids multiplying a credential that must be treated as compromised.
