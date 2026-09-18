#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "python-dotenv"]
# ///
"""Register a folder of process definitions with SEPEX.

Reads <folder>/*/*.yml and adds or replaces each process. SEPEX_URL must be set
in the environment or the repo's .env. USE_LOCAL_IMAGES selects locally built
":local" images; GPU_AVAILABLE selects the GPU variant of each process.

Usage:
    just register-sepex-processes-local
    just register-sepex-processes-cloud

    uv run --script sepex/register_processes.py <folder>

Example:
    uv run --script sepex/register_processes.py sepex/local/plugins
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

_TRUE = {"true", "1", "yes", "y", "on"}


def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


GHCR_REPO = "ghcr.io/ngwpc/twod-fim-jobs"
PUBLISHED_TAG = "dev"


def resolve_image(image: str, use_local: bool) -> str:
    """The image to register, given $USE_LOCAL_IMAGES. See module docstring."""
    if use_local or not image.endswith(":local"):
        return image
    return f"{GHCR_REPO}/{image.removesuffix(':local')}:{PUBLISHED_TAG}"


def wanted_hardware(process_id: str, gpu: bool) -> bool:
    """Whether this process id is the hardware variant $GPU_AVAILABLE calls for."""
    if process_id.endswith("Cpu"):
        return not gpu
    if process_id.endswith("Gpu"):
        return gpu
    return True


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
    image = definition.get("host", {}).get("image")
    method = "PUT" if process_id in served else "POST"
    status, text = request(method, f"{base_url}/processes/{process_id}", definition)
    if status == 200:
        verb = "replaced" if method == "PUT" else "added"
        print(f"  {verb:8} {process_id}{f'  ({image})' if image else ''}")
        return True

    print(f"  FAILED   {process_id}: {method} -> {status}: {text[:300]}")
    if method == "PUT" and status == 500:
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

    use_local, gpu = env_true("USE_LOCAL_IMAGES"), env_true("GPU_AVAILABLE")
    print(
        f"Registering process(es) from {folder} with {base_url} "
        f"(USE_LOCAL_IMAGES={use_local}, GPU_AVAILABLE={gpu})"
    )
    wait_for(base_url)
    served = served_ids(base_url)
    defined: set[str] = set()
    failures = 0
    for path in definitions:
        definition = yaml.safe_load(path.read_text())
        process_id = definition["info"]["id"]
        if not wanted_hardware(process_id, gpu):
            print(f"  skipped  {process_id} (GPU_AVAILABLE={gpu})")
            continue
        defined.add(process_id)
        if "image" in definition.get("host", {}):
            definition["host"]["image"] = resolve_image(definition["host"]["image"], use_local)
        if not register(base_url, definition, served):
            failures += 1

    for process_id in sorted(served - defined):
        print(f"  (not ours, left alone) {process_id}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
