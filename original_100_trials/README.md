> **Completed-study note (2026-11-03):** The original 100-trial batch was completed and passed technical QC (100 trials, 3,000 test events). This README preserves the implementation-stage guidance; statements below about tests not yet being run describe the original pre-experiment state. See the repository-root README for verified outcomes and limitations.

> **v0.3 startup fix:** The AI server now supplies `HOME=/root` because systemd-run may omit that environment variable. Full experiments require validation on the actual Ubuntu VM.

# MemoryGuard

**Mac users: begin with [`START_HERE.md`](START_HERE.md).** The new one-go
launcher can create the Ubuntu VM, run the pilot, verify memory pressure,
run the 100-trial matrix and transfer raw logs back to the Mac. It stops if
experimental quality checks fail. All VM experiments still need to be run.

Keeping a security monitor on time when a local AI model fills up memory.

**Student:** Jaweria Amer Chaudhry  
**Course:** Operating Systems  
**Project type:** One-person experimental project on Linux memory management and security  
**Status:** Initial code and documentation. The real Ubuntu VM experiments have **not** been run.

## The question I am testing

When a local AI model creates memory pressure, what is the smallest `memory.low`
setting that helps a security monitor detect a test event within 250 ms? I also
want to see whether increasing CPU priority makes a difference and whether
protecting the monitor slows down the AI.

The test is deliberately narrow. It does not use real malware. A harmless Python
script opens a fake file, and an eBPF monitor reports the event. I measure the
time from the file-open request to the Python rule checker's alert.

## What is in this folder

| File | Purpose |
|---|---|
| `src/memoryguard/attack.py` | Opens harmless files with unique event IDs and records trigger times. |
| `src/memoryguard/monitor.py` | Runs bpftrace and records each matching alert. |
| `src/memoryguard/ai_load.py` | Runs a local Ollama model and stress-ng inside the same AI cgroup. |
| `src/memoryguard/prepare_model.py` | Downloads the fixed model *before* any measured trials. |
| `src/memoryguard/runner.py` | Starts the test services using systemd, verifies cgroups and saves trial logs. |
| `src/memoryguard/analysis.py` | Matches alerts with triggers and calculates on-time rates and delays. |
| `src/memoryguard/plots.py` | Makes the three main figures from actual trial summaries only. |
| `scripts/bootstrap_ubuntu.sh` | Installs Ubuntu test dependencies and creates the research slice. |
| `tests/test_analysis.py` | Offline tests for event parsing and measurement calculations. |
| `report/REPORT_OUTLINE.md` | Working report outline, pending the professor's Part 4 template. |
| `PROJECT_NOTES.md` | Decisions, limitations and items not yet verified. |

## Environment

Use an **Ubuntu 24.04 Linux VM**, not the Mac itself. The proposal uses 8 GB RAM,
4 vCPUs and a small CPU-only Ollama model. On an Apple Silicon Mac, Multipass
can create an ARM64 Ubuntu VM. Everything in these steps happens in that VM.

The test uses:

- Linux cgroup v2 memory controller (`memory.low`)
- `bpftrace` to observe `openat()` calls from the harmless test process
- Python standard library for event IDs, timestamps and CSV/JSON output
- `stress-ng` to apply extra memory load
- Ollama and one fixed model (`qwen2.5:1.5b` for the first run)
- `systemd-run` to place the monitor and AI in separate services/cgroups

`memory.low` is not a fixed RAM reservation. It is a best-effort preference
under reclaim. I record the value in the monitor cgroup **and its parent** so I
can check that the setting was applied. Actual memory pressure must still be
confirmed from the readings, not assumed from `stress-ng` command arguments.

## Before running any privileged code

Preview the experiment matrix without needing Linux or root:

```bash
PYTHONPATH=src python3 -m memoryguard.runner --plan
```

This prints the proposed 100 conditions and does not start any processes.

## Step 1 - Create the disposable VM (on the Mac)

After installing Multipass, open Terminal on the Mac:

```bash
multipass launch 24.04 --name memoryguard --cpus 4 --memory 8G --disk 32G
multipass transfer -r MemoryGuard_Starter memoryguard:/home/ubuntu/
multipass shell memoryguard
```

The transfer command assumes the ZIP has been extracted in the current Mac
folder. In the Ubuntu VM:

```bash
cd ~/MemoryGuard_RunKit
sudo bash scripts/bootstrap_ubuntu.sh
```

**Only execute the setup inside this disposable VM.** Memory pressure can
freeze an under-provisioned VM and cause an OOM kill. It must not be run on a
school system, live server or main macOS installation.

## Step 2 - Install the local model runtime (in the VM)

Get Ollama from its official Linux installation instructions:
https://docs.ollama.com/linux

If using the official install script, review it first and run it **inside the
VM**. For experiments, the normal Ollama system service must be stopped because
it would put the model outside the test AI cgroup:

```bash
sudo systemctl stop ollama.service
sudo systemctl disable ollama.service
```

Then prepare the chosen model, outside any timed trial:

```bash
cd ~/MemoryGuard_RunKit
sudo env PYTHONPATH="$PWD/src" python3 -m memoryguard.prepare_model \
  --model qwen2.5:1.5b
```

This needs internet access once to download the model. After it has finished,
the experiment itself uses the model stored in `/var/lib/memoryguard/models`.
There is no need to download a model during a trial.

## Step 3 - Test the monitor first

```bash
cd ~/MemoryGuard_RunKit
PYTHONPATH=src python3 -m unittest discover -s tests -v
sudo env PYTHONPATH="$PWD/src" python3 -m memoryguard.runner --pilot-monitor
```

The first command checks the analysis logic offline. The second starts the
kernel tracer and opens **five** fake files. There is no AI load in this pilot.
The output goes under `results/pilot_monitor_*/...`.

The trial should contain:

- `attacks.jsonl`: event IDs and monotonic start times
- `alerts.jsonl`: matching alert IDs and receipt times
- `monitor_status.json`: tracer status
- `cgroup_snapshots.json`: actual cgroup memory and pressure readings
- `summary.json`: on-time fraction, median delay and p99 delay
- `matched.csv`: one row per fake event

If this pilot does not catch any opens, **do not continue to the 100 runs**.
Check the bpftrace logs and ensure the VM has the `sys_enter_openat` tracepoint.
The script sets `BPFTRACE_MAX_STRLEN=192` because Ubuntu 24.04 bpftrace
otherwise truncates long pathnames before the event ID.
Use `sudo bpftrace -l 'tracepoint:syscalls:sys_enter_openat'` and, if needed,
`sudo strace -e trace=openat python3 ...` to check the open syscall.

## Step 4 - Test the complete path once

```bash
sudo env PYTHONPATH="$PWD/src" python3 -m memoryguard.runner --pilot-full
```

This starts both the monitor and a local Ollama service in separate systemd
cgroups. It runs a short test with no stress load. The AI service includes
Ollama itself, its model subprocess and (when enabled) stress-ng. This matters:
we cannot measure the AI group's memory if only the stress program is in it.

Before the full run, inspect the pilot's `summary.json`, `ai_metrics.jsonl`,
`cgroup_snapshots.json`, and both `*_status.json` files. Make sure alerts and
AI token throughput are both present.

## Step 5 - Calibrate pressure, then run the comparisons

The planned comparisons are:

| Condition | Monitor setting |
|---|---|
| `baseline` | `memory.low=0`, normal CPU weight and nice value |
| `cpu_priority` | `memory.low=0`, higher CPU weight and `nice=-10` |
| `low_64m` | `memory.low=64 MiB` |
| `low_128m` | `memory.low=128 MiB` |
| `low_256m` | `memory.low=256 MiB` |

The three memory values are **starting candidates**, not findings. I need to
measure the monitor's usage and calibrate these values if they are too high or
low. Likewise, the four `stress-ng` allocations must be chosen based on whether
the VM shows genuine memory pressure without constantly killing the workload.

The proposed matrix is 5 settings x 4 pressure levels x 5 repeats = 100 trials.
After successful pilots and calibration, the command is:

```bash
sudo env PYTHONPATH="$PWD/src" python3 -m memoryguard.runner --all \
  --pressures 0,1024,2048,3072 --repeats 5 --attacks 30 --interval 2
```

**Important:** The example pressure numbers are not validated on this VM. They
must be adjusted based on actual `memory.pressure`, `memory.stat` and
`memory.events` readings. The full runner is initial code and still needs a
complete end-to-end trial on the target VM. Do not run a long batch until the
pilot and a few manual high-pressure tests succeed.

## How the measurements work

The attacker records `time.monotonic_ns()` immediately before the harmless
`os.open` call. The monitor records `time.monotonic_ns()` when the Python rule
checker receives the matching eBPF event. Both use the same Linux clock.

```text
alert delay = Python checker receipt time - trigger time
on-time detection rate = events received within 250 ms / events sent
```

A missing alert is a failed detection, not a latency of zero or 250 ms. The p99
metric is calculated only from observed alerts. Both the on-time rate and the
number of misses must be reported together.

AI speed uses Ollama's `eval_count / eval_duration * 1,000,000,000`. It is
recorded separately from any time spent loading the model. A major page fault
or an OOM counter increase is evidence to interpret, not an automatically
proved explanation for a late alert.

## Final figures (after collecting real data)

Install matplotlib with apt during Ubuntu bootstrap, or with `pip` in a venv:

```bash
PYTHONPATH=src python3 -m memoryguard.plots results --output figures
```

This makes detection-rate, p99-delay and AI-throughput plots. The plotter
refuses to create graphs when no valid observed trial results are present.

## Cleaning up

After the experiment:

```bash
sudo systemctl stop memoryguard.slice
```

Stopping the research slice ends anything still running in it. Keep the real
JSON/CSV measurements and the environment file. Do not delete experimental
logs until the report and graphs have been checked.

## Limits and what remains

This is a starter implementation, **not** proof that the hypothesis is true.
The code has local unit tests, but eBPF, systemd service membership, the
ARM64 Ollama runtime and all 100 trial combinations still require testing in
the specified Ubuntu VM. The final paper needs real results, a GitHub upload,
and the exact report template that the professor said would be shared later.

If the monitor never experiences late alerts, that is still a valid outcome.
The aim is to find out what happens, not force the graph to look like the
prediction.

## Technical references

- Linux kernel cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- systemd resource control: https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html
- bpftrace file-open tutorial: https://bpftrace.org/tutorial-one-liners
- Ollama API usage metrics: https://github.com/ollama/ollama/blob/main/docs/api/usage.mdx
- Multipass install: https://canonical.com/multipass/docs/stable/how-to-guides/install-multipass/


## Resuming a stopped full batch (v0.4)

For the November 3 original experiment on the Mac, the completed trial data
stay in the *original* Multipass VM. Do not delete that VM or replace its
`results/batch_20261103T052931Z` directory. After installing v0.4 code into
the existing `~/Downloads/MemoryGuard_RunKit` folder, run:

```bash
cd ~/Downloads/MemoryGuard_RunKit
bash scripts/run_mac.sh resume
```

The script requires the original lab VM, confirms the batch's environment,
and skips only the files it can validate. It archives incomplete attempts
under `results/_failed_attempts` for study, not deletion. A resumed run saves
new raw measurements and updates `memoryguard_outputs.tar.gz` on the Mac. If
more than one full batch exists, specify the exact intended source:

```bash
MG_RESUME_BATCH=batch_20261103T052931Z bash scripts/run_mac.sh resume
```

Real inference errors may recur. This is a research failure to investigate, not
something to conceal by repeatedly declaring success. Postrun QC checks
coverage and measured memory PSI, but do not prove the hypothesized benefit
of `memory.low`. The first 59 valid trials show no monitor major faults or
file refaults, so a truthful report must explicitly discuss this limitation.
