#!/usr/bin/env python3
"""Deploy the Dagster orchestrator on EC2 using docker-compose.cloud.yaml.

Run from the repo root on EC2. Reads image references from .env
(ORCHESTRATOR_IMAGE, BUILD_MODEL_IMAGE). CLI args override .env values.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.cloud.yaml"
ENV_FILE = REPO_ROOT / ".env"
HEALTH_TIMEOUT_S = 60
HEALTH_POLL_S = 3


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f".env not found at {path}\nCreate it from example.cloud.env before running.")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key and value:
            env[key.strip()] = value.strip()
    return env


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def wait_for_healthy() -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "twodfim-code-server"],
            capture_output=True,
            text=True,
        )
        status = result.stdout.strip()
        if status == "healthy":
            return True
        print(f"  code-server: {status or 'starting'}...")
        time.sleep(HEALTH_POLL_S)
    return False


def main() -> None:
    env = read_env(ENV_FILE)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--orchestrator-image",
        default=env.get("ORCHESTRATOR_IMAGE"),
        help="Orchestrator image (default: ORCHESTRATOR_IMAGE from .env)",
    )
    parser.add_argument(
        "--build-model-image",
        default=env.get("BUILD_MODEL_IMAGE"),
        help="Build model image (default: BUILD_MODEL_IMAGE from .env)",
    )
    args = parser.parse_args()

    if not args.orchestrator_image or not args.build_model_image:
        sys.exit("ORCHESTRATOR_IMAGE and BUILD_MODEL_IMAGE must be set in .env or passed via CLI args.")

    if not COMPOSE_FILE.exists():
        sys.exit(f"Compose file not found: {COMPOSE_FILE}")

    orchestrator_image = args.orchestrator_image
    build_model_image = args.build_model_image

    print(f"\nImages:")
    print(f"  orchestrator: {orchestrator_image}")
    print(f"  build_model:  {build_model_image}")

    print("\nPulling images...")
    run(["docker", "pull", orchestrator_image])
    run(["docker", "pull", build_model_image])

    print("\nStopping existing services...")
    run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False)

    print("\nStarting services...")
    compose_env = dict(os.environ, ORCHESTRATOR_IMAGE=orchestrator_image, BUILD_MODEL_IMAGE=build_model_image)
    print(f"  $ docker compose -f {COMPOSE_FILE} up -d")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        check=True,
        env=compose_env,
    )

    print(f"\nWaiting for code-server health ({HEALTH_TIMEOUT_S}s timeout)...")
    if wait_for_healthy():
        print("  code-server is healthy.")
    else:
        print("  WARNING: code-server did not become healthy within timeout.")
        print("  Check logs: docker compose -f docker-compose.cloud.yaml logs code-server")

    print("\nService status:")
    run(["docker", "compose", "-f", str(COMPOSE_FILE), "ps"])


if __name__ == "__main__":
    main()
