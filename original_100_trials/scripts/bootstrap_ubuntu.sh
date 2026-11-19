#!/usr/bin/env bash
# Run inside the disposable Ubuntu 24.04 VM only.
set -euo pipefail
if [[ $(id -u) -ne 0 ]]; then
  echo "Run as: sudo bash scripts/bootstrap_ubuntu.sh" >&2
  exit 1
fi
if [[ ! -e /run/systemd/system ]]; then
  echo "This environment is not an Ubuntu systemd VM." >&2
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    bpftrace stress-ng python3 python3-venv python3-matplotlib \
    curl strace ca-certificates
cat > /etc/systemd/system/memoryguard.slice <<'SLICE'
[Unit]
Description=MemoryGuard research lab slice

[Slice]
MemoryAccounting=yes
CPUAccounting=yes
MemoryLow=0
SLICE
systemctl daemon-reload
systemctl start memoryguard.slice
systemctl set-property --runtime memoryguard.slice MemoryLow=0 MemoryAccounting=yes
if [[ ! -f /sys/fs/cgroup/cgroup.controllers ]]; then
  echo "ERROR: cgroup v2 is not available" >&2
  exit 1
fi
if ! grep -qw memory /sys/fs/cgroup/cgroup.controllers; then
  echo "ERROR: cgroup v2 memory controller not enabled" >&2
  exit 1
fi
echo "Installed Ubuntu lab dependencies and started memoryguard.slice"
echo "Next: install Ollama from its official Linux instructions, inside this VM."
echo "Then run prepare_model.py and pilot tests from README.md."
