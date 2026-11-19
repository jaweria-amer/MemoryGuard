"""CPU-only Ollama generator on a dedicated port, outside the reclaim-test slice."""
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
from .common import json_write

HOST = 'http://127.0.0.1:11436'
PROMPT = 'Explain one important reason for using memory cgroups to isolate processes.'


def request_json(path, payload=None, timeout=90):
    data = json.dumps(payload).encode('utf8') if payload is not None else None
    req = urllib.request.Request(HOST + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def generation(model):
    return request_json('/api/generate', {
        'model': model, 'prompt': PROMPT, 'stream': False, 'keep_alive': '10m',
        'options': {'num_predict': 48, 'num_ctx': 2048, 'temperature': 0,
                    'num_thread': 2},
    })


def rate(result):
    count = result.get('eval_count', 0)
    duration = result.get('eval_duration', 0)
    if not isinstance(count, (int, float)) or not isinstance(duration, (int, float)):
        return None
    return round(count * 1e9 / duration, 3) if count > 0 and duration > 0 else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--model', default='qwen2.5:1.5b')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    stopped = False
    server = None
    state = a.out / 'ai_status.json'
    json_write(state, {'state': 'starting'})
    def handler(*_):
        nonlocal stopped
        stopped = True
        if server and server.poll() is None:
            server.terminate()
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    try:
        env = os.environ.copy()
        env.update({'HOME': '/root', 'OLLAMA_HOST': '127.0.0.1:11436',
                    'OLLAMA_MODELS': str(a.model_dir), 'OLLAMA_NUM_PARALLEL': '1',
                    'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_KEEP_ALIVE': '10m',
                    'CUDA_VISIBLE_DEVICES': '-1'})
        with (a.out / 'ollama_stderr.log').open('w', encoding='utf8') as err, \
                (a.out / 'ollama_stdout.log').open('w', encoding='utf8') as std, \
                (a.out / 'ai_metrics.jsonl').open('w', encoding='utf8', buffering=1) as log:
            server = subprocess.Popen(['ollama', 'serve'], env=env, stdout=std, stderr=err)
            limit = time.monotonic() + 100
            while not stopped and time.monotonic() < limit:
                if server.poll() is not None:
                    raise RuntimeError('Ollama server exited before ready; see ollama_stderr.log')
                try:
                    request_json('/api/tags', timeout=4)
                    break
                except (urllib.error.URLError, TimeoutError, OSError):
                    time.sleep(0.5)
            else:
                raise TimeoutError('Ollama server failed to become ready')
            def gen_once():
                started = time.monotonic_ns()
                response = generation(a.model)
                speed = rate(response)
                if speed is None:
                    raise RuntimeError('Ollama returned no valid token-generation speed')
                finished = time.monotonic_ns()
                log.write(json.dumps({'start_ns': started, 'finish_ns': finished,
                                      'tokens_per_second': speed,
                                      'eval_count': response.get('eval_count')}) + '\n')
                log.flush()
                return speed
            gen_once()  # Model loaded and real inference verified before starting trials.
            json_write(state, {'state': 'ready', 'model': a.model, 'cpu_thread_option': 2})
            while not stopped:
                gen_once()
        json_write(state, {'state': 'stopped'})
    except BaseException as exc:
        if not stopped:
            json_write(state, {'state': 'error', 'reason': str(exc)})
            print('AI service error:', exc, file=sys.stderr, flush=True)
            raise
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=6)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()

if __name__ == '__main__':
    main()
