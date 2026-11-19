"""Run a local Ollama model and memory load inside ONE systemd cgroup.

The model server and any child runner stay in this service cgroup. A default
ollama.service outside the cgroup would invalidate the comparison, so we use
a separate local port and a model directory prepared before the experiment.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:11435"
PROMPT = "Explain in two short paragraphs why a Linux process can experience a major page fault."


def http_json(path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def status_write(path: Path, state: str, reason: str = "") -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "reason": reason,
                               "monotonic_ns": time.monotonic_ns()}), encoding="utf-8")
    tmp.replace(path)


def generate(model: str, timeout: float = 120.0) -> dict:
    return http_json("/api/generate", {
        "model": model, "prompt": PROMPT, "stream": False,
        "keep_alive": "10m", "options": {"num_predict": 96, "temperature": 0, "num_ctx": 2048},
    }, timeout=timeout)


def build_ollama_env(model_dir: Path) -> dict[str, str]:
    """Set the environment required for an isolated systemd-run Ollama child.

    systemd transient services may omit HOME even for root. Ollama needs it,
    irrespective of its explicit OLLAMA_MODELS directory.
    """
    env = os.environ.copy()
    env["HOME"] = "/root"  # disposable VM service runs as root
    env.update({"OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(model_dir),
                "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1"})
    return env


def run(output: Path, model: str, model_dir: Path, pressure_mb: int) -> None:
    if pressure_mb < 0:
        raise ValueError("pressure_mb must be nonnegative")
    output.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    status_path = output / "ai_status.json"
    status_write(status_path, "starting")
    ollama_proc = None
    stress_proc = None
    stop = False

    def handler(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    try:
        env = build_ollama_env(model_dir)
        with (output / "ollama_stderr.log").open("w", encoding="utf-8") as err:
            ollama_proc = subprocess.Popen(["ollama", "serve"], env=env,
                                           stdout=err, stderr=subprocess.STDOUT)
            ready_by = time.monotonic() + 90
            while time.monotonic() < ready_by and not stop:
                if ollama_proc.poll() is not None:
                    raise RuntimeError("Ollama server exited; see ollama_stderr.log")
                try:
                    tags = http_json("/api/tags", timeout=3)
                    break
                except (urllib.error.URLError, TimeoutError, ValueError):
                    time.sleep(1)
            else:
                raise RuntimeError("Ollama server did not start in 90 seconds")
            installed = {m.get("name") for m in tags.get("models", [])}
            if model not in installed:
                raise RuntimeError(f"Model {model!r} absent in {model_dir}. Prepare it before testing.")
            # Warm up model first, then start stress. This avoids measuring loading as throughput.
            generate(model)
            if pressure_mb:
                stress_proc = subprocess.Popen(
                    ["stress-ng", "--vm", "1", "--vm-bytes", f"{pressure_mb}M",
                     "--vm-keep", "--metrics-brief", "--timeout", "15m"],
                    stdout=err, stderr=subprocess.STDOUT)
                time.sleep(1)
                if stress_proc.poll() is not None:
                    raise RuntimeError("stress-ng exited during startup; reduce pressure")
            status_write(status_path, "ready")
            with (output / "ai_metrics.jsonl").open("w", encoding="utf-8", buffering=1) as out:
                while not stop:
                    started = time.monotonic_ns()
                    try:
                        data = generate(model)
                        n = int(data.get("eval_count", 0))
                        duration_ns = int(data.get("eval_duration", 0))
                        value = (1e9 * n / duration_ns) if n and duration_ns > 0 else None
                        result = {"start_monotonic_ns": started,
                                  "tokens_generated": n,
                                  "eval_duration_ns": duration_ns,
                                  "tokens_per_second": value,
                                  "model": model}
                    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                        result = {"start_monotonic_ns": started, "error": str(exc),
                                  "tokens_per_second": None}
                    out.write(json.dumps(result, sort_keys=True) + "\n")
                    out.flush()
                    if stress_proc is not None and stress_proc.poll() is not None:
                        status_write(status_path, "error", "stress-ng stopped unexpectedly")
                        return
            status_write(status_path, "stopped")
    except Exception as exc:
        status_write(status_path, "error", str(exc))
        print(f"AI load error: {exc}", file=sys.stderr, flush=True)
        raise
    finally:
        for proc in (stress_proc, ollama_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default="qwen2.5:1.5b")
    p.add_argument("--model-dir", type=Path, default=Path("/var/lib/memoryguard/models"))
    p.add_argument("--pressure-mb", type=int, default=0)
    args = p.parse_args()
    run(args.output, args.model, args.model_dir, args.pressure_mb)


if __name__ == "__main__":
    main()
