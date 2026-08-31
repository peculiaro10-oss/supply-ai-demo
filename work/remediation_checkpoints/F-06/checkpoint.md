# F-06 pre-change checkpoint

- Created: 2026-08-30 Africa/Lagos
- Scope: active `.env` driver prefix, `main.py`, and `tests/test_infrastructure.py`
- Pre-change `.env` SHA-256: `2F21CBBED16AF0BCDD35906DC961BA0EE63F683F52892074509247E500A06B7B`
- Pre-change `main.py` SHA-256: `1DD14FE90A9F4DC980D18949A0DAB2EC4DA0203B9EDB295969EC97EDF360D933`
- Pre-change `tests/test_infrastructure.py` SHA-256: `B68D9C7521CE4E2512F3D706D12BA076082CB559BB647410E46E811C5F403B27`
- Root cause evidence: the active URL prefix was bare `postgresql`, while requirements pin Psycopg 3 and do not install psycopg2. The production guard also rejected the documented explicit `postgresql+psycopg://` form.
- Secret retention policy: the URL value is not copied into this checkpoint.
