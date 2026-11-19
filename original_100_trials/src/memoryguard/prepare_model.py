"""Download the fixed Ollama model before timed experiments begin.

Only run this inside the disposable Ubuntu VM, once, with sudo.
The preparation server stops before any measured trial starts.
"""
import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:11435"


def api(path: str, body: dict | None = None, timeout: int = 120) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(HOST + path, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--model-dir", type=Path, default=Path("/var/lib/memoryguard/models"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this only inside the Ubuntu VM with sudo.")
    args.model_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(args.model_dir)})
    process = subprocess.Popen(["ollama", "serve"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError("Ollama exited while preparing the model")
            try:
                api("/api/tags", timeout=3)
                break
            except (urllib.error.URLError, TimeoutError, ValueError):
                time.sleep(1)
        else:
            raise RuntimeError("Ollama server did not respond")
        print(f"Preparing {args.model} in {args.model_dir} (not part of a trial).", flush=True)
        print("The first download requires internet access inside the VM.", flush=True)
        result = api("/api/pull", {"model": args.model, "stream": False}, timeout=3600)
        if result.get("status") != "success":
            raise RuntimeError(f"Unexpected model download response: {result}")
        tags = api("/api/tags")
        models = {x.get("name") for x in tags.get("models", [])}
        if args.model not in models:
            raise RuntimeError("Model download finished but the model is not listed")
        print(f"Prepared: {args.model}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
