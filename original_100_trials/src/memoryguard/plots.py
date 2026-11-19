"""Create figures from real per-trial results. No predicted data is plotted."""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_rows(root: Path) -> list[dict]:
    rows = []
    for p in root.rglob("summary.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        trial = data.get("trial", {})
        if not trial.get("experimental_valid", False):
            continue
        if not all(x in trial for x in ("condition", "pressure_mb", "repeat")):
            continue
        rows.append({"condition": trial["condition"],
                     "pressure_mb": trial["pressure_mb"],
                     "repeat": trial["repeat"],
                     "on_time_pct": data["on_time_pct"],
                     "p99_delay_ms": data.get("p99_observed_delay_ms"),
                     "ai_tokens_sec": data.get("ai_tokens_per_second_median")})
    return rows


def make_plots(results: Path, output: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = load_rows(results)
    if not rows:
        raise ValueError("No observed trial summaries found. Do not generate imaginary graphs.")
    output.mkdir(parents=True, exist_ok=True)
    saved = []
    specs = [
        ("on_time_pct", "On-time detection (%)", "01_detection_vs_pressure.png"),
        ("p99_delay_ms", "Observed alert p99 (ms)", "02_latency_vs_pressure.png"),
        ("ai_tokens_sec", "AI throughput (tokens/sec)", "03_ai_speed_vs_pressure.png"),
    ]
    for metric, ylabel, filename in specs:
        fig, ax = plt.subplots(figsize=(7.5, 4.4))
        groups = defaultdict(lambda: defaultdict(list))
        for row in rows:
            if row[metric] is not None:
                groups[row["condition"]][row["pressure_mb"]].append(row[metric])
        for label, by_pressure in sorted(groups.items()):
            levels = sorted(by_pressure)
            y = [statistics.median(by_pressure[p]) for p in levels]
            ax.plot(levels, y, marker="o", label=label)
        ax.set_xlabel("Memory pressure: stress-ng allocation (MiB)")
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel + " across repeated trials")
        ax.grid(alpha=0.25)
        if groups:
            ax.legend(fontsize=8)
        if metric == "on_time_pct":
            ax.set_ylim(0, 101)
        fig.tight_layout()
        dest = output / filename
        fig.savefig(dest, dpi=170)
        plt.close(fig)
        saved.append(dest)
    return saved


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    p.add_argument("--output", type=Path, default=Path("figures"))
    args = p.parse_args()
    for path in make_plots(args.results, args.output):
        print(path)


if __name__ == "__main__":
    main()
