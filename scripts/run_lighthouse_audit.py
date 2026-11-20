# ruff: noqa: T201
"""Run Google Lighthouse Accessibility audit on the Omnea application and output score."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8008
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "services" / "api" / "src"

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(src_dir)
        + os.pathsep
        + str(repo_root / "packages" / "procure_core" / "src")
        + os.pathsep
        + str(repo_root / "services" / "workers" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"

    print(f"Starting uvicorn server on {BASE_URL}...")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            SERVER_HOST,
            "--port",
            str(SERVER_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(src_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server
    import urllib.request

    ready = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=1.0) as res:
                if res.status == 200:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(0.5)

    if not ready:
        print("ERROR: Server did not become ready.")
        server.terminate()
        return 1

    report_file = repo_root / "lighthouse_accessibility.json"
    if report_file.exists():
        report_file.unlink()

    print(f"Running Lighthouse accessibility audit on {BASE_URL}...")
    cmd = [
        "npx.cmd" if sys.platform == "win32" else "npx",
        "lighthouse",
        f"{BASE_URL}/",
        "--only-categories=accessibility",
        "--output=json",
        "--chrome-flags=--headless",
        f"--output-path={report_file}",
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print("Lighthouse CLI output:")
        print(res.stdout[:500])

        if not report_file.exists():
            print("ERROR: Lighthouse did not produce report file.")
            print(res.stderr[:500])
            return 1

        with open(report_file, encoding="utf-8") as f:
            data = json.load(f)

        score = data["categories"]["accessibility"]["score"] * 100
        print("\n=======================================================")
        print(f"  LIGHTHOUSE ACCESSIBILITY SCORE: {score:.0f} / 100")
        print("=======================================================\n")

        audits = data.get("audits", {})
        passed_audits = [
            a["title"] for a in audits.values() if a.get("score") == 1 and "title" in a
        ]
        failed_audits = [
            f"{a['title']}: {a.get('explanation', a.get('description', ''))}"
            for a in audits.values()
            if a.get("score") is not None and a.get("score") < 1 and "title" in a
        ]

        print(f"Passed accessibility audits count: {len(passed_audits)}")
        if failed_audits:
            print("Audits needing attention:")
            for fa in failed_audits[:5]:
                print(f"  - {fa}")
        else:
            print("All accessibility audits passed!")

        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
