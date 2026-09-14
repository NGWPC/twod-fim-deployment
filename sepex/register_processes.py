#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "python-dotenv"]
# ///
"""Register one folder of process definitions with SEPEX.

SEPEX is a separate service. POST /processes/{id} adds a process and
PUT /processes/{id} replaces one. Running it twice is safe as long as
process definitions do not change between runs.

The target is $SEPEX_URL, read from the repo's .env the same way the loop
reads it: processes belong on the SEPEX the loop submits to.

Usage:
  uv run sepex/register_processes.py sepex/local/plugins
  uv run sepex/register_processes.py sepex/cloud/plugins
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def request(method: str, url: str, body: dict | None = None) -> tuple[int, str]:
    """Status and body of one call. An HTTP error is an answer, not an exception."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def wait_for(base_url: str, seconds: int = 60) -> None:
    """Return once SEPEX answers, so this can run straight after the stack starts."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            request("GET", f"{base_url}/processes?f=json&limit=1")
            return
        except urllib.error.URLError as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"SEPEX not reachable at {base_url}: {exc.reason}")
            time.sleep(2)


def served_ids(base_url: str) -> set[str]:
    """Every process id SEPEX serves. The list is paged, at most 100 per page."""
    ids: set[str] = set()
    offset = 0
    while True:
        status, text = request(
            "GET", f"{base_url}/processes?f=json&limit=100&offset={offset}"
        )
        if status != 200:
            raise RuntimeError(f"GET /processes -> {status}: {text[:300]}")
        page = json.loads(text)["processes"]
        ids.update(p["id"] for p in page)
        if len(page) < 100:
            return ids
        offset += 100


def register(base_url: str, definition: dict, served: set[str]) -> bool:
    """Add or replace one process. True when SEPEX accepted it."""
    process_id = definition["info"]["id"]
    method = "PUT" if process_id in served else "POST"
    status, text = request(method, f"{base_url}/processes/{process_id}", definition)
    if status == 200:
        print(f"  {'replaced' if method == 'PUT' else 'added':8} {process_id}")
        return True

    print(f"  FAILED   {process_id}: {method} -> {status}: {text[:300]}")
    if method == "PUT" and status == 500:
        # SEPEX replaces a process by moving <PLUGINS_DIR>/<id>/<id>.yml aside,
        # which is where it writes the ones registered through this API. A
        # process it loaded from a plugins folder at startup lives under
        # another name, so it can be neither replaced nor deleted this way.
        print(
            "           It was probably loaded from files at startup, not registered "
            "here. Clear SEPEX's plugins folder once, restart it, and rerun."
        )
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    base_url = os.environ.get("SEPEX_URL", "").rstrip("/")
    if not base_url:
        print("SEPEX_URL is not set (in the environment or the repo's .env)")
        return 2

    definitions = sorted(folder.glob("*/*.yml"))
    if not definitions:
        print(f"No process definitions in {folder}/*/*.yml")
        return 2

    print(f"Registering {len(definitions)} process(es) from {folder} with {base_url}")
    wait_for(base_url)
    served = served_ids(base_url)
    defined: set[str] = set()
    failures = 0
    for path in definitions:
        definition = yaml.safe_load(path.read_text())
        defined.add(definition["info"]["id"])
        if not register(base_url, definition, served):
            failures += 1

    for process_id in sorted(served - defined):
        print(f"  (not ours, left alone) {process_id}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
