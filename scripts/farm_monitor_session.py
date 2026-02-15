#!/usr/bin/env python3
"""
Farm monitoring session - watches profiles for 10 minutes,
captures all issues, performance metrics, and activity patterns.
Exits early if last profile shows no activity for 3 minutes.
"""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

MAX_DURATION = 600  # 10 minutes
INACTIVITY_TIMEOUT = 180  # 3 minutes
CHECK_INTERVAL = 15  # check every 15 seconds

PROJECT_ROOT = Path(__file__).parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "profiles"


def get_profile_status(name):
    """Check profile process status via /proc."""
    proc_root = Path("/proc")
    pids = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            if b"run.py" not in cmdline:
                continue
            env_bytes = (entry / "environ").read_bytes()
            for item in env_bytes.split(b"\x00"):
                if item.startswith(b"OCR_PROFILE_SUFFIX="):
                    profile = item.split(b"=", 1)[1].decode("utf-8", "ignore")
                    if profile == name:
                        pids.append(int(entry.name))
                    break
        except Exception:
            continue
    return pids


def get_log_info(name):
    """Get log file info for a profile."""
    log_file = LOG_DIR / f"{name}.log"
    if not log_file.exists():
        return {"exists": False, "size": 0, "last_modified": None, "tail": ""}

    stat = log_file.stat()
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            lines = content.strip().split("\n")
            tail = "\n".join(lines[-30:]) if lines else ""
    except Exception as e:
        tail = f"Error reading: {e}"

    return {
        "exists": True,
        "size": stat.st_size,
        "last_modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "tail": tail,
        "total_lines": len(lines) if "lines" in dir() else 0,
    }


def get_system_metrics():
    """Collect system metrics."""
    metrics = {}

    # Memory
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = int(parts[1].strip().split()[0])
                    meminfo[key] = value
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            if total > 0:
                used = total - available
                metrics["mem_used_mb"] = round(used / 1024)
                metrics["mem_total_mb"] = round(total / 1024)
                metrics["mem_pct"] = round(100 * used / total, 1)
    except Exception:
        pass

    # CPU load
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            metrics["load_1m"] = float(parts[0])
            metrics["load_5m"] = float(parts[1])
    except Exception:
        pass

    # Per-profile memory (RSS)
    for name in PROFILES:
        pids = get_profile_status(name)
        total_rss = 0
        for pid in pids:
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            total_rss += int(line.split()[1])
                            break
            except Exception:
                pass
        if pids:
            metrics[f"rss_{name}_mb"] = round(total_rss / 1024)

    return metrics


def count_errors_in_log(name):
    """Count error-related lines in profile log."""
    log_file = LOG_DIR / f"{name}.log"
    if not log_file.exists():
        return {"errors": 0, "warnings": 0, "critical": 0, "recent_errors": []}

    errors = 0
    warnings = 0
    critical = 0
    recent_errors = []

    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_lower = line.lower()
                if "error" in line_lower or "exception" in line_lower or "traceback" in line_lower:
                    errors += 1
                    recent_errors.append(line.strip()[:200])
                    recent_errors = recent_errors[-10:]  # keep last 10
                elif "warning" in line_lower or "warn" in line_lower:
                    warnings += 1
                elif "critical" in line_lower or "fatal" in line_lower:
                    critical += 1
    except Exception:
        pass

    return {
        "errors": errors,
        "warnings": warnings,
        "critical": critical,
        "recent_errors": recent_errors,
    }


def check_log_growth(name, prev_sizes):
    """Check if log file is growing (activity indicator)."""
    log_file = LOG_DIR / f"{name}.log"
    current_size = log_file.stat().st_size if log_file.exists() else 0
    prev_size = prev_sizes.get(name, 0)
    prev_sizes[name] = current_size
    return current_size > prev_size, current_size - prev_size


def check_prompts_sent(name):
    """Count 'Prompt sent' occurrences in log."""
    log_file = LOG_DIR / f"{name}.log"
    if not log_file.exists():
        return 0
    count = 0
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "Prompt sent" in line or "prompt sent" in line:
                    count += 1
    except Exception:
        pass
    return count


PROFILES = ["1985chauhongtrang", "2014edyta"]


def main():
    start_time = time.time()
    prev_sizes = {}
    last_activity_time = start_time
    check_num = 0
    all_snapshots = []

    print(f"=== Farm Monitor Session Started at {datetime.now(UTC).isoformat()} ===")
    print(f"Monitoring profiles: {PROFILES}")
    print(f"Max duration: {MAX_DURATION}s, Inactivity timeout: {INACTIVITY_TIMEOUT}s")
    print("=" * 80)

    while True:
        elapsed = time.time() - start_time
        if elapsed >= MAX_DURATION:
            print(f"\n>>> Duration limit reached ({MAX_DURATION}s). Stopping.")
            break

        inactive_time = time.time() - last_activity_time
        if inactive_time >= INACTIVITY_TIMEOUT and check_num > 2:
            print(f"\n>>> No activity detected for {int(inactive_time)}s. Early exit.")
            break

        check_num += 1
        now = datetime.now(UTC).isoformat()
        snapshot = {"check": check_num, "time": now, "elapsed_s": round(elapsed)}

        print(f"\n--- Check #{check_num} | Elapsed: {int(elapsed)}s | {now} ---")

        any_activity = False

        for name in PROFILES:
            pids = get_profile_status(name)
            log_info = get_log_info(name)
            growing, growth = check_log_growth(name, prev_sizes)
            error_info = count_errors_in_log(name)
            prompts = check_prompts_sent(name)

            if growing:
                any_activity = True
                last_activity_time = time.time()

            profile_data = {
                "name": name,
                "running": len(pids) > 0,
                "pids": pids,
                "log_size": log_info.get("size", 0),
                "log_growth_bytes": growth,
                "log_growing": growing,
                "errors": error_info["errors"],
                "warnings": error_info["warnings"],
                "prompts_sent": prompts,
            }
            snapshot[name] = profile_data

            status = "🟢 RUNNING" if pids else "🔴 STOPPED"
            print(f"  [{name}] {status} PIDs={pids}")
            print(f"    Log: {log_info.get('size', 0)} bytes, Growing: {growing} (+{growth}b)")
            print(
                f"    Errors: {error_info['errors']}, Warnings: {error_info['warnings']}, Prompts: {prompts}"
            )

            if error_info["recent_errors"]:
                print(f"    Recent errors:")
                for err in error_info["recent_errors"][-3:]:
                    print(f"      ! {err[:150]}")

        # System metrics
        sys_metrics = get_system_metrics()
        snapshot["system"] = sys_metrics
        print(
            f"  [SYSTEM] Mem: {sys_metrics.get('mem_used_mb', '?')}/{sys_metrics.get('mem_total_mb', '?')}MB ({sys_metrics.get('mem_pct', '?')}%)"
        )
        print(
            f"           Load: {sys_metrics.get('load_1m', '?')} (1m), {sys_metrics.get('load_5m', '?')} (5m)"
        )
        for name in PROFILES:
            rss_key = f"rss_{name}_mb"
            if rss_key in sys_metrics:
                print(f"           RSS {name}: {sys_metrics[rss_key]}MB")

        all_snapshots.append(snapshot)

        # Wait for next check
        time.sleep(CHECK_INTERVAL)

    # Final summary
    print("\n" + "=" * 80)
    print("=== FINAL SUMMARY ===")

    total_elapsed = time.time() - start_time
    print(f"Total monitoring time: {int(total_elapsed)}s ({len(all_snapshots)} checks)")

    for name in PROFILES:
        last = all_snapshots[-1].get(name, {})
        first = all_snapshots[0].get(name, {}) if all_snapshots else {}

        print(f"\n  [{name}]:")
        print(f"    Final status: {'RUNNING' if last.get('running') else 'STOPPED'}")
        print(f"    Log growth: {first.get('log_size', 0)} -> {last.get('log_size', 0)} bytes")
        print(f"    Total errors: {last.get('errors', 0)}")
        print(f"    Total warnings: {last.get('warnings', 0)}")
        print(f"    Prompts sent: {last.get('prompts', 0)}")

        # Check for crashes (profile started but then stopped)
        was_running = any(s.get(name, {}).get("running", False) for s in all_snapshots)
        is_running = last.get("running", False)
        if was_running and not is_running:
            print(f"    ⚠️ CRASH DETECTED: Profile was running but then stopped")
        elif not was_running:
            print(f"    ⚠️ NEVER STARTED: Profile never showed as running")

    # Save full report
    report_file = PROJECT_ROOT / "logs" / "farm_monitor_report.json"
    with open(report_file, "w") as f:
        json.dump(all_snapshots, f, indent=2, default=str)
    print(f"\nFull report saved to: {report_file}")

    # Print log tails for analysis
    print("\n" + "=" * 80)
    print("=== LOG TAILS ===")
    for name in PROFILES:
        log_info = get_log_info(name)
        print(f"\n--- {name} (last 30 lines) ---")
        print(log_info.get("tail", "No log"))


if __name__ == "__main__":
    main()
