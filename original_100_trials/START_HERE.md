> **Completed-study note (2026-11-03):** This is the original setup guide. The controlled 100-trial research batch has since been completed; historical references below to unrun trials reflect the earlier development stage. Use the root README to find the validated results and the safe no-stress reproduction path.

# MemoryGuard: one-go Mac-to-Ubuntu runbook (v0.3)

**Status: an instrumented research prototype, not a finished research report.**
The Python analysis tests work in the current development environment. Real eBPF,
Ollama, stress-ng and 100 comparisons have NOT been run by the author of this kit.
They need your disposable Ubuntu virtual machine.

## The shortest Mac route

**Prerequisites:** macOS 13+, 16+ GB Mac memory (24 GB recommended), 40+ GB free
disk, administrator access, and internet access for initial installation.
Do **not** use an old 8 GB Intel Mac or run stress-ng directly in macOS.

1. Install **Canonical Multipass** from its official installer:
   <https://canonical.com/multipass/install>
   Or, if Homebrew is already installed:
   `brew install --cask multipass`
2. Unzip `MemoryGuard_RunKit_v0.2.zip` into `~/Downloads`.
3. Open macOS **Terminal** and run:

   ```bash
   cd ~/Downloads/MemoryGuard_RunKit
   bash scripts/run_mac.sh all
   ```

The launcher creates an isolated **Ubuntu 24.04 VM (8 GB RAM, 4 vCPUs, 40 GB disk)**,
transfers the source, installs tools from Ubuntu's package repositories and the
official Ollama Linux installer, downloads the fixed model, runs unit tests,
two pilots, then a four-trial smoke/pressure calibration, then (ONLY if all checks
pass) the full **100 trial** design, quality control and graphs.

It uses the `caffeinate` command on the host to reduce idle sleep while testing;
keep the Mac plugged in and do not close Terminal until the command returns.

## Safeguards and stopping rules

The scripts are written to refuse execution outside the specially marked
Ubuntu VM. The smoke gate stops if it finds no measured cgroup memory PSI
stall-time, an OOM kill, or missing event/AI data. **The default pressure levels
are proposed starting values, not a measured calibration.** Thus a first run
may stop with a QC failure; that is the correct scientific response. Do not
turn a failed smoke result into a final conclusion.

For a shorter initial pass, on the Mac use:

```bash
cd ~/Downloads/MemoryGuard_RunKit
bash scripts/run_mac.sh pilot
```

That runs through the smoke test and stops before the 100-trial batch.
If it passes, run `bash scripts/run_mac.sh all` to run the full sequence.

If smoke QC reports **no PSI stalls**, check `results/smoke_*/qc.json` and the
matching `trial_table.csv` before modifying anything. After assessing actual
RAM availability, you can choose a different *four-level* pressure matrix. The
script refuses allocations over 6144 MiB, and this cap is not a guarantee of
safety on a heavily loaded VM. Example **syntax only** (NOT a validated setting):

```bash
MG_PRESSURES="0,2048,4096,5120" bash scripts/run_mac.sh pilot
```

If a trial fails or stress-ng is killed, lower the maximum allocation.
Do not switch to live workloads or disable the guard checks to force completion.

## Expected outputs on the Mac

- `results/pilot_monitor_*/...`: kernel monitor verification
- `results/pilot_full_*/...`: monitor + local AI pilot
- `results/smoke_*/...`: four preliminary comparisons and QC
- `results/batch_*/...`: final 100-trial logs when successful
- `results/batch_*/trial_table.csv`: one row per trial
- `results/batch_*/condition_table.csv`: grouped rates/delay/AI speed
- `results/batch_*/qc.json`: quality-control verdict
- `figures/`: three graphs created from recorded values only
- `run_logs/`: command output and failures

If the workflow stops, the Mac launcher **still copies available logs and results
back from the VM** for troubleshooting.

## Inspect the results

On the Mac:

```bash
cd ~/Downloads/MemoryGuard_RunKit
find results -name qc.json -print
ls -lh figures/
```

For failed tests in the VM:

```bash
multipass shell memoryguard-lab
cd ~/MemoryGuard_RunKit
ls -t run_logs/ | head
sudo journalctl -u 'mg-monitor-*' -n 50 --no-pager
```

To inspect resource configuration:

```bash
multipass info memoryguard-lab
```

After copying **all results to the Mac**, stop (not delete) the VM:

```bash
multipass stop memoryguard-lab
```

Don't delete the VM or raw results until the report and reproducibility check
are finished.

## What remains after data collection

The one-go runner cannot write honest results before the experiments are run.
It also cannot replace Jaweria's understanding or the required faculty review.
Even with all 100 trials completed, the project still needs:

1. Manual analysis of pressure/latency/AI-speed trade-offs, including negative
   or inconclusive findings.
2. A final report following the professor's **Project - Part 4 (sample)** format.
3. A published GitHub repository and a clean-room rerun of setup/test commands.
4. The presentation and viva practice, with Jaweria understanding every core
   architectural choice and being able to defend it.

Share the `memoryguard_outputs.tar.gz` file or the complete `results` and
`figures` directories for the next analysis/report stage. Do not turn synthetic
unit-test fixtures into graphs for the submitted report.

## Official references

- <https://canonical.com/multipass/docs/stable/reference/command-line-interface/transfer/>
- <https://docs.ollama.com/linux>
- <https://www.freedesktop.org/software/systemd/man/systemd.resource-control.html>
- <https://docs.kernel.org/admin-guide/cgroup-v2.html>

## v0.3 fix (2026-11-03)

Ollama under the experimental transient systemd service failed with `Error: $HOME is not defined`. The AI child process now explicitly sets `HOME=/root` inside the disposable VM; nothing is changed on the Mac host. The VM was verified to launch, Python unit tests passed, model prepared, and the standalone eBPF pilot passed. The corrected combined pilot and 100-trial batch are **not yet verified**. Reuse the same VM; do not delete it.
