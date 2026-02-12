"""Port Listener Map — show listening ports and associated processes."""

import re

from homelab.modules.transport import ssh_run
from homelab.ui import C, scrollable_list, error


def show_port_map(host, port=None):
    """Show all listening TCP/UDP ports with process info."""
    result = ssh_run(
        "ss -tlnp 2>/dev/null; echo '---SEP---'; ss -ulnp 2>/dev/null",
        host=host, port=port,
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Failed to get port information.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    sections = result.stdout.split("---SEP---")
    rows = []

    for si, section in enumerate(sections):
        proto = "tcp" if si == 0 else "udp"
        for line in section.strip().split("\n"):
            if line.startswith("State") or line.startswith("Netid") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            # ss output: State Recv-Q Send-Q Local_Address:Port Peer_Address:Port [Process]
            local_addr = parts[3] if len(parts) > 3 else "?"
            if ":" in local_addr:
                addr_part, port_part = local_addr.rsplit(":", 1)
            else:
                addr_part, port_part = local_addr, "?"

            # Extract process name from users:(("name",pid=N,...))
            process = ""
            rest = " ".join(parts[4:])
            m = re.search(r'\("([^"]+)"', rest)
            if m:
                process = m.group(1)

            rows.append(
                f"  {proto:<5} {port_part:>6}  {addr_part:<25} {C.ACCENT}{process}{C.RESET}"
            )

    if not rows:
        error("No listening ports found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Sort by port number
    def sort_key(row):
        try:
            return int(row.split()[1])
        except (ValueError, IndexError):
            return 99999

    rows.sort(key=sort_key)

    header = f"  {C.DIM}{'Proto':<5} {'Port':>6}  {'Address':<25} {'Process'}{C.RESET}"
    scrollable_list("Port Map", rows, header_line=header)
