# Project structure

The app is split into a **backend** (FastAPI/Python) and a **frontend**
(static web client). One server process serves both from the same origin.

```
supply-ai/
├── backend/                  FastAPI server
│   ├── main.py               all API routes, models, business logic
│   ├── storage.py            private upload storage providers
│   ├── upcitemdb_provider.py barcode-catalog lookup
│   └── sms_service.py        SMS integration hook
│
├── frontend/                 the web client (served by backend/main.py)
│   ├── index.html            app shell — <head> + all page/modal markup
│   ├── css/
│   │   ├── base.css          scrollbars, POS cart, inputs, misc component CSS
│   │   └── dashboard-fixes.css  narrow-screen dashboard/layout overrides
│   ├── js/
│   │   ├── app.js            the entire single-page app (one classic script)
│   │   └── heartbeat.js      presence heartbeat bootstrap
│   ├── assets/               icons, manifest.json, vendored Tailwind/FA/qr-code
│   └── sw.js                 service worker (app-shell caching)
│
├── alembic/ + alembic.ini    database migrations (run from repo root)
├── scripts/                  one-off operational scripts
├── tests/ + conftest.py      pytest suite
├── Dockerfile                production image
└── .env / .env.example       configuration
```

## How the pieces connect

- `backend/main.py` computes `PROJECT_ROOT` as its parent directory and loads
  the frontend from `PROJECT_ROOT/frontend`:
  - `GET /` → `frontend/index.html`
  - `GET /sw.js` → `frontend/sw.js`
  - `/assets/*`, `/css/*`, `/js/*` → mounted from `frontend/assets|css|js`
  - `.env` and the default `uploads/` dir are read from the repo root.
- `index.html` references `/css/base.css`, `/css/dashboard-fixes.css`,
  `/js/app.js`, `/js/heartbeat.js`, and `/assets/...` by absolute path, so the
  same file works under the web deployment and the Capacitor mobile shell.
- `app.js` is a **classic script** (not an ES module): every function is global,
  which is what the inline `onclick="..."` handlers in `index.html` rely on.
  Load order matters — keep `app.js` before `heartbeat.js`.

## Running it

```bash
# dev server (uses .claude/launch.json → backend/main.py on :8000)
venv/Scripts/python.exe backend/main.py

# or explicitly
venv/Scripts/python.exe -m uvicorn main:app --app-dir backend --port 8000
```

`--app-dir backend` (also in the Dockerfile CMD) puts `backend/` on the import
path so `import main` works. Tests get the same via the root `conftest.py`;
Alembic gets it via `prepend_sys_path = . backend` in `alembic.ini`.

## Editing the frontend

- A page's markup lives in `frontend/index.html` (search for its section
  comment, e.g. `<!-- SIDEBAR NAVIGATION -->`).
- Its behavior lives in `frontend/js/app.js` (search for the handler name from
  the markup's `onclick`).
- Styling is Tailwind utility classes in the markup; the two `css/` files only
  hold what Tailwind can't express inline.
- After changing anything in `frontend/`, regenerate the mobile copy before a
  Capacitor build: `rm -rf www && cp -r frontend www` (see MOBILE_PACKAGING.md).
