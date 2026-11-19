# MemoryGuard - final report working outline

Student: Jaweria Amer Chaudhry
Course: Operating Systems
Status: Working outline, not a final submission

The university says the final report must follow the "Project - Part 4 (sample)" format.
That sample was not included with the materials received, so this outline must
be rearranged to match it when it becomes available.

## 1. Introduction

A security monitor needs to react when something suspicious happens. The problem
I am studying is whether a local AI model can make that reaction late by using
too much RAM. I am focusing on memory pressure, not just CPU usage.

## 2. Research question

When a local AI model creates memory pressure, what is the smallest cgroup v2
memory.low setting that helps a security monitor detect a test event within
250 ms? I also compare this with no protection and higher CPU priority, and
measure the cost in AI tokens per second.

## 3. Related OS concepts

- Reclaim, working set and major page faults
- cgroup v2 memory.low (best-effort protection, not reserved RAM)
- Scheduling priority as a comparison
- Kernel tracing using bpftrace/eBPF

Cite primary Linux kernel and systemd documentation in the final report.

## 4. Experimental setup

Record actual values from the Linux VM rather than copying guessed numbers:
- Ubuntu release, kernel, architecture, memory and vCPUs
- bpftrace, stress-ng, Ollama versions
- Local model name, exact model identifier, context size
- cgroup paths, memory.low of parent and monitor
- Trigger count, intervals and deadline

## 5. Method

The test script creates harmless files with unique IDs, and a separate process
opens them. bpftrace observes those openat calls. A small Python rule checker
records when each matching event arrives. The on-time detection rate is the
number of events reported within 250 ms divided by the total number sent.
Missed events are counted as failures.

I repeat the same tests at four calibrated pressure levels. I compare the
baseline, CPU priority, and three memory.low settings, with at least five
repeats per combination. The source plan is 5 x 4 x 5 = 100 trial runs.

## 6. Results (fill only with observed data)

[Insert recorded test totals here. Do not fabricate values.]

Figure 1: On-time detection versus memory pressure.
Figure 2: Observed p99 delay versus memory pressure.
Figure 3: AI throughput (tokens/sec) versus memory pressure.

Report any trials with OOM kills, failed tracers, or data loss separately.

## 7. Discussion

Explain whether the hypothesis was supported. If even the baseline is always
on time, that is a meaningful negative result. It is better to report a
negative result than to manufacture a performance cliff.

Discuss possible limitations: the monitor is small, only one VM and one model
are tested, and the fake event is not a real attack or production EDR.

## 8. Conclusion

[Write after experiments and analysis.]

## 9. References

- Linux kernel documentation: cgroup v2 Memory Interface Files
- systemd.resource-control: MemoryLow and CPUWeight
- bpftrace tutorial: tracing file opens
- Ollama API: performance fields

[Add the lecturer's required citation style once provided.]
