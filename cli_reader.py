# timed_reader.py
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

# Import your reader class from the package
from thermocouple_reader.reader import ThermocoupleReader


def iso_now_local() -> str:
    # Local time ISO-like string (no timezone suffix). Easy to read in Excel.
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_out_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"thermocouple_{ts}.csv"


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read thermocouple temperatures periodically and save to CSV."
    )
    p.add_argument("--port", default="COM3", help='Serial port, e.g. "COM3" (Windows).')
    p.add_argument("--baudrate", type=int, default=9600, help="Serial baudrate.")
    p.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Sampling interval in seconds (e.g. 5).",
    )
    p.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of samples to collect. 0 means run forever until Ctrl+C.",
    )
    p.add_argument(
        "--out",
        default="",
        help="CSV output path. Default: ./thermocouple_YYYYmmdd_HHMMSS.csv",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to existing CSV instead of overwriting/creating a new one.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.interval <= 0:
        print("ERROR: --interval must be > 0", file=sys.stderr)
        return 2

    out_path = args.out.strip() or default_out_path()
    ensure_parent_dir(out_path)

    file_exists = os.path.exists(out_path)
    open_mode = "a" if (args.append or file_exists) else "w"
    write_header = not (args.append or file_exists)

    reader = ThermocoupleReader(port=args.port, baudrate=args.baudrate)
    reader.open()

    # If open failed, reader.serial would likely be None or not open.
    if not reader.serial or not getattr(reader.serial, "is_open", False):
        print(f"ERROR: Failed to open serial port {args.port}", file=sys.stderr)
        return 1

    print(f"Reading from {args.port} every {args.interval}s -> {out_path}")
    if args.count > 0:
        print(f"Will collect {args.count} samples.")
    else:
        print("Will run until Ctrl+C.")

    samples_written = 0

    try:
        with open(out_path, open_mode, newline="", encoding="utf-8") as f:
            w = csv.writer(f)

            if write_header:
                w.writerow(["timestamp_local", "ch1_c", "ch2_c", "ch3_c", "ch4_c"])

            # Use a monotonic clock to keep stable intervals even if reads take time.
            next_t = time.monotonic()

            while True:
                # Wait until next scheduled time
                now_m = time.monotonic()
                sleep_s = next_t - now_m
                if sleep_s > 0:
                    time.sleep(sleep_s)

                # Schedule the next tick *before* reading, so drift is minimized.
                next_t += args.interval

                temps = reader.read_temperatures()

                ts = iso_now_local()
                if temps and len(temps) >= 4:
                    ch1, ch2, ch3, ch4 = temps[:4]
                else:
                    ch1 = ch2 = ch3 = ch4 = None

                w.writerow([ts, ch1, ch2, ch3, ch4])
                f.flush()

                samples_written += 1
                print(f"[{ts}] ch1={ch1} ch2={ch2} ch3={ch3} ch4={ch4}")

                if args.count > 0 and samples_written >= args.count:
                    break

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C).")
    finally:
        reader.close()
        print(f"Done. Samples written: {samples_written}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
