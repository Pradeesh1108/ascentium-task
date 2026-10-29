#!/usr/bin/env python3
"""Cross-platform runner for the Ticket Intelligence System.

Works identically on macOS, Linux, and Windows.
Starts both the FastAPI server (serving API + Ticket Desk UI)
and the background processing worker concurrently.
"""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"


def get_command_prefix():
    """Detects whether `uv` is available on the system PATH."""
    if shutil.which("uv"):
        return ["uv", "run"]
    # Fallback to current python interpreter
    return [sys.executable, "-m"]


def main():
    if not BACKEND_DIR.exists():
        print(f"Error: Backend directory not found at {BACKEND_DIR}", file=sys.stderr)
        sys.exit(1)

    prefix = get_command_prefix()
    use_uv = prefix[0] == "uv"
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8000")

    # Command definitions
    if use_uv:
        api_cmd = ["uv", "run", "uvicorn", "app.main:app", "--host", host, "--port", port, "--reload"]
        worker_cmd = ["uv", "run", "python", "-m", "app.worker"]
    else:
        api_cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", port]
        worker_cmd = [sys.executable, "-m", "app.worker"]

    print("=" * 64)
    print("  Ticket Desk — Customer Support Intelligence System")
    print("=" * 64)
    print(f"  Working Directory: {BACKEND_DIR}")
    print("  Starting Services:")
    print("    • FastAPI Backend + Ticket Desk UI (Port 8000)")
    print("    • Background Processing Worker")
    print("=" * 64)
    print("  Access URLs:")
    print("    • Operations Screen:  http://127.0.0.1:8000/")
    print("    • Interactive Docs:   http://127.0.0.1:8000/docs")
    print("    • System Health:      http://127.0.0.1:8000/health")
    print("    • Metrics & Stats:    http://127.0.0.1:8000/stats")
    print("=" * 64)
    print("  Press Ctrl+C to stop all services.\n")

    processes = []
    try:
        # Launch FastAPI server
        api_proc = subprocess.Popen(api_cmd, cwd=str(BACKEND_DIR))
        processes.append(("FastAPI Server", api_proc))

        # Short pause to let server start binding
        time.sleep(1)

        # Launch Worker process
        worker_proc = subprocess.Popen(worker_cmd, cwd=str(BACKEND_DIR))
        processes.append(("Worker", worker_proc))

        # Monitor both processes
        while True:
            for name, proc in processes:
                ret = proc.poll()
                if ret is not None:
                    print(f"\n[Warning] {name} exited unexpectedly with code {ret}")
                    return
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nShutting down Ticket Intelligence System...")
    finally:
        for name, proc in processes:
            if proc.poll() is None:
                print(f"Terminating {name} (PID {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("All services stopped.")


if __name__ == "__main__":
    main()

