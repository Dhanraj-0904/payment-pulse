"""Create a compact normal-to-incident-to-recovery chart from a scenario CSV."""

import argparse
from collections import defaultdict
from datetime import datetime
from statistics import mean

from simulator.loader import load_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot payment-health metrics over time.")
    parser.add_argument("--input", required=True, help="Scenario CSV input path.")
    parser.add_argument("--output", required=True, help="PNG output path.")
    parser.add_argument("--window-seconds", type=int, default=60)
    args = parser.parse_args()
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("Install matplotlib to create charts: pip install matplotlib") from exc

    buckets: dict[datetime, list] = defaultdict(list)
    for record in load_csv(args.input):
        epoch = int(record.timestamp.timestamp()) // args.window_seconds * args.window_seconds
        buckets[datetime.fromtimestamp(epoch, tz=record.timestamp.tzinfo)].append(record)
    times = sorted(buckets)
    success = [sum(row.status == "SUCCESS" for row in buckets[time]) / len(buckets[time]) for time in times]
    failure = [1 - value for value in success]
    latency = [mean(row.latency_ms for row in buckets[time]) for time in times]
    figure, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    axes[0].plot(times, success, color="green"); axes[0].set_ylabel("Success rate")
    axes[1].plot(times, failure, color="crimson"); axes[1].set_ylabel("Failure rate")
    axes[2].plot(times, latency, color="darkorange"); axes[2].set_ylabel("Latency (ms)")
    axes[2].set_xlabel("Time")
    figure.suptitle("Payment Pulse incident replay: normal -> incident -> recovery")
    figure.tight_layout()
    figure.savefig(args.output, dpi=160)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
