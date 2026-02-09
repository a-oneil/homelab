"""Speedtest plugin — run local speed tests, track history with trends."""

import subprocess
import sys
import time

from homelab.config import CFG, save_config
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, info, error, warn, bar_chart


class SpeedtestPlugin(Plugin):
    name = "Speedtest"
    key = "speedtest"

    def is_configured(self):
        return True  # Always available

    def get_config_fields(self):
        return []

    def get_menu_items(self):
        return [
            ("Speedtest            — speed test with history", speedtest_menu),
        ]

    def get_actions(self):
        return {
            "Speedtest": ("speedtest_menu", speedtest_menu),
            "Run Speed Test": ("speedtest_run", _run_local_test),
        }

    def get_dashboard_widgets(self):
        history = CFG.get("speedtest_history", [])
        if not history:
            return []
        last = history[-1]
        dl = last.get("download", 0)
        ul = last.get("upload", 0)
        ping = last.get("ping", 0)
        ts = last.get("timestamp", "")
        when = ts[:16] if ts else "?"
        return [{
            "title": "Speedtest",
            "lines": [
                f"Down: {dl:.1f} Mbps  Up: {ul:.1f} Mbps  Ping: {ping:.1f} ms",
                f"{C.DIM}Last run: {when}{C.RESET}",
            ],
        }]


def speedtest_menu():
    while True:
        idx = pick_option("Speedtest:", [
            "Run Speed Test        — test this machine's connection",
            "View History          — past results with trends",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 2:
            continue
        elif idx == 3:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(SpeedtestPlugin())
        elif idx == 0:
            _run_local_test()
        elif idx == 1:
            _view_history()


def _parse_speedtest_output(output):
    """Parse speedtest-cli --simple output into dict."""
    result = {"ping": 0, "download": 0, "upload": 0}
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("Ping:"):
            try:
                result["ping"] = float(line.split(":")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.startswith("Download:"):
            try:
                val = float(line.split(":")[1].strip().split()[0])
                unit = line.split(":")[1].strip().split()[1] if len(line.split(":")[1].strip().split()) > 1 else "Mbit/s"
                if "Gbit" in unit:
                    val *= 1000
                elif "Kbit" in unit:
                    val /= 1000
                result["download"] = val
            except (ValueError, IndexError):
                pass
        elif line.startswith("Upload:"):
            try:
                val = float(line.split(":")[1].strip().split()[0])
                unit = line.split(":")[1].strip().split()[1] if len(line.split(":")[1].strip().split()) > 1 else "Mbit/s"
                if "Gbit" in unit:
                    val *= 1000
                elif "Kbit" in unit:
                    val /= 1000
                result["upload"] = val
            except (ValueError, IndexError):
                pass
    return result


def _save_result(result):
    """Append a speed test result to history."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "download": result["download"],
        "upload": result["upload"],
        "ping": result["ping"],
    }
    history = CFG.get("speedtest_history", [])
    history.append(entry)
    # Keep last 50 results
    if len(history) > 50:
        history = history[-50:]
    CFG["speedtest_history"] = history
    save_config(CFG)
    return entry


def _run_local_test():
    """Run speedtest-cli on the local machine."""
    info("Running speed test (this may take 20-30 seconds)...")
    print()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "speedtest", "--simple"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        error("Speed test timed out.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    if result.returncode != 0:
        error(f"Speed test failed: {result.stderr.strip()[:100]}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    parsed = _parse_speedtest_output(result.stdout)
    if not parsed["download"] and not parsed["upload"]:
        error("Could not parse speed test results.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    entry = _save_result(parsed)
    _print_result(entry)
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _print_result(entry):
    """Display a single speed test result."""
    dl = entry["download"]
    ul = entry["upload"]
    ping = entry["ping"]

    print(f"\n  {C.BOLD}Speed Test Results{C.RESET}\n")
    max_speed = max(dl, ul, 100)  # At least 100 Mbps scale
    print(f"  Download:  {bar_chart(dl, max_speed, width=25)}  {C.BOLD}{dl:.1f} Mbps{C.RESET}")
    print(f"  Upload:    {bar_chart(ul, max_speed, width=25)}  {C.BOLD}{ul:.1f} Mbps{C.RESET}")
    print(f"  Ping:      {C.BOLD}{ping:.1f} ms{C.RESET}")


def _view_history():
    """Show speed test history with trends."""
    history = CFG.get("speedtest_history", [])
    if not history:
        warn("No speed test history yet. Run a test first.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    lines = []
    lines.append(f"\n  {C.BOLD}Speed Test History{C.RESET}  ({len(history)} results)\n")
    lines.append(f"  {C.BOLD}{'Date':<20} {'Down':>10} {'Up':>10} {'Ping':>8}{C.RESET}")
    lines.append(f"  {'─' * 50}")

    for entry in reversed(history[-20:]):
        ts = entry.get("timestamp", "?")[:16]
        dl = entry.get("download", 0)
        ul = entry.get("upload", 0)
        ping = entry.get("ping", 0)

        # Color-code download speed
        if dl >= 500:
            dl_color = C.GREEN
        elif dl >= 100:
            dl_color = C.YELLOW
        else:
            dl_color = C.RED

        lines.append(
            f"  {ts:<20} "
            f"{dl_color}{dl:>8.1f}M{C.RESET}  "
            f"{ul:>8.1f}M  "
            f"{ping:>6.1f}ms"
        )

    # Trend summary
    if len(history) >= 2:
        recent = history[-5:]
        avg_dl = sum(e["download"] for e in recent) / len(recent)
        avg_ul = sum(e["upload"] for e in recent) / len(recent)
        avg_ping = sum(e["ping"] for e in recent) / len(recent)
        lines.append(f"\n  {C.BOLD}Recent Average (last {len(recent)}):{C.RESET}")
        lines.append(f"  Down: {avg_dl:.1f} Mbps  Up: {avg_ul:.1f} Mbps  Ping: {avg_ping:.1f} ms")

    lines.append("")
    header = "\n".join(lines)
    pick_option("", ["← Back"], header=header)
