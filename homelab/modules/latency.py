"""Network Latency Matrix — ping all hosts and display RTT."""

import re
import subprocess

from homelab.ui import C, error


def show_latency_matrix(hosts):
    """Ping each host from local machine, display latency table."""
    if not hosts:
        error("No hosts configured.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Network Latency Matrix{C.RESET}\n")
    print(f"  {C.DIM}{'Host':<25} {'Address':<25} {'RTT':>10} {'Status':>8}{C.RESET}")
    print(f"  {'─' * 70}")

    for h in hosts:
        name = h.get("name", "?")
        host_addr = h.get("host", "")
        # Strip user@ prefix for ping target
        actual_host = host_addr.split("@")[-1] if "@" in host_addr else host_addr
        # Strip port from host:port format
        if ":" in actual_host and not actual_host.startswith("["):
            actual_host = actual_host.split(":")[0]

        if not actual_host:
            print(f"  {name:<25} {'(no host)':<25} {C.DIM}{'—':>10} {'—':>8}{C.RESET}")
            continue

        print(f"  {name:<25} {actual_host:<25} ", end="", flush=True)

        try:
            result = subprocess.run(
                ["ping", "-c", "3", "-W", "2", actual_host],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                # Parse average RTT — handles both macOS and Linux formats
                # Linux: rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms
                # macOS: round-trip min/avg/max/stddev = 0.123/0.456/0.789/0.012 ms
                m = re.search(r'[\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms', result.stdout)
                if m:
                    avg_ms = float(m.group(1))
                    if avg_ms < 10:
                        color = C.GREEN
                    elif avg_ms < 50:
                        color = C.YELLOW
                    else:
                        color = C.RED
                    print(f"{color}{avg_ms:>8.1f}ms{C.RESET} {C.GREEN}{'UP':>8}{C.RESET}")
                else:
                    print(f"{C.GREEN}{'OK':>10}{C.RESET} {C.GREEN}{'UP':>8}{C.RESET}")
            else:
                print(f"{C.RED}{'timeout':>10}{C.RESET} {C.RED}{'DOWN':>8}{C.RESET}")
        except subprocess.TimeoutExpired:
            print(f"{C.RED}{'timeout':>10}{C.RESET} {C.RED}{'DOWN':>8}{C.RESET}")
        except Exception:
            print(f"{C.RED}{'error':>10}{C.RESET} {C.RED}{'ERR':>8}{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
