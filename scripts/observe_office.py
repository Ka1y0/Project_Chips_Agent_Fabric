"""Run the real read-only Office and idle host together in a fresh local directory."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from threading import Event
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="new directory (default: a unique temp dir)")
    parser.add_argument("--port", type=int, default=7330)
    parser.add_argument("--check", action="store_true", help="exit after observing a local NOOP")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="chips-office-"))
    else:
        data_dir = args.data_dir.absolute()
        try:
            data_dir.mkdir(mode=0o700)
        except OSError as error:
            parser.error(f"a new, unused data directory is required: {error}")

    env = {k: v for k, v in os.environ.items() if not k.startswith("PROJECT_SUPERVISOR_")}
    env.update(
        PROJECT_SUPERVISOR_HOST="127.0.0.1",
        PROJECT_SUPERVISOR_PORT=str(args.port),
        PROJECT_SUPERVISOR_READ_ONLY_API="true",
    )
    cli = [sys.executable, "-m", "project_supervisor.cli", "--data-dir", str(data_dir)]
    host = f"observe-{uuid.uuid4().hex}"
    base = f"http://127.0.0.1:{args.port}"
    opener = build_opener(ProxyHandler({}))
    stopping = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    children: list[subprocess.Popen] = []
    print(f"Isolated data directory (retained on exit): {data_dir}", flush=True)
    try:
        subprocess.run([*cli, "init"], env=env, check=True)
        children.append(
            subprocess.Popen([*cli, "serve", "--allow-unauthenticated-loopback"], env=env)
        )
        children.append(
            subprocess.Popen(
                [
                    *cli,
                    "autonomous",
                    "serve",
                    "--host-id",
                    host,
                    "--max-concurrent-goals",
                    "1",
                    "--metabolic-interval-seconds",
                    "2",
                    "--max-daily-wakes",
                    "0",
                    "--max-daily-inspections",
                    "0",
                    "--exploration-budget-cap",
                    "0",
                ],
                env=env,
            )
        )
        deadline = time.monotonic() + 25
        ready = False
        while not stopping.is_set():
            if any(child.poll() is not None for child in children):
                raise RuntimeError("Office or host exited unexpectedly (check port availability)")
            if not ready:
                try:
                    with opener.open(base + "/v1/autonomy/heartbeat?limit=10", timeout=1) as resp:
                        ticks = json.load(resp)["data"]["ticks"]
                    if any(t["host_id"] == host and t["result"] == "NOOP" for t in ticks):
                        with opener.open(base + "/v1/capabilities", timeout=1) as resp:
                            if json.load(resp)["data"]["mutations"]:
                                raise RuntimeError("expected a read-only API")
                        with opener.open(base + "/office/", timeout=1) as resp:
                            if resp.status != 200:
                                raise RuntimeError("Office assets unavailable")
                        print(f"Observed NOOP. Read-only Office: {base}/office/", flush=True)
                        ready = True
                        if args.check:
                            return 0
                except (URLError, TimeoutError, ConnectionError):
                    pass  # The API and host start independently.
                if not ready and time.monotonic() >= deadline:
                    raise RuntimeError("isolated host did not report a NOOP before timeout")
            stopping.wait(0.25)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Observation failed: {error}", file=sys.stderr)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
