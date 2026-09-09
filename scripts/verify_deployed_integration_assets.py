"""Compare public integration assets with the exact source files in this tree."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "/js/email-return.js": ROOT / "frontend" / "js" / "email-return.js",
    "/auth/email-verified": ROOT / "frontend" / "email-verified.html",
    "/js/app.js": ROOT / "frontend" / "js" / "app.js",
    "/js/payments.js": ROOT / "frontend" / "js" / "payments.js",
}


def normalized(data: bytes) -> bytes:
    text = data.decode("utf-8-sig").replace("\r\n", "\n")
    return text.encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(normalized(data)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("CAULDRA_DEPLOYMENT_URL", ""))
    args = parser.parse_args()
    base = args.base_url.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise SystemExit("CAULDRA_DEPLOYMENT_URL/--base-url must be a public HTTPS origin")

    failures = []
    for route, source_path in ASSETS.items():
        response = requests.get(base + route, timeout=20, allow_redirects=False)
        response.raise_for_status()
        expected = digest(source_path.read_bytes())
        actual = digest(response.content)
        state = "MATCH" if actual == expected else "STALE"
        print(f"{route} {state} local_sha256={expected} deployed_sha256={actual}")
        if state != "MATCH":
            failures.append(route)
    if failures:
        print("DEPLOYED_INTEGRATION_ASSET_PARITY_FAILED routes=" + ",".join(failures))
        return 1
    print("DEPLOYED_INTEGRATION_ASSET_PARITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
