"""Firewall Viewer — display iptables/nftables rules."""

import subprocess

from homelab.modules.transport import ssh_run
from homelab.ui import C, scrollable_list, info


def show_firewall_rules(host, port=None):
    """Try nftables first, fall back to iptables via TTY (sudo password support)."""
    # Try nftables (passwordless sudo)
    result = ssh_run("sudo -n nft list ruleset 2>/dev/null", host=host, port=port)
    if result.returncode == 0 and result.stdout.strip():
        lines = result.stdout.strip().split("\n")
        rows = [f"  {line}" for line in lines]
        header = f"  {C.BOLD}Firewall Rules (nftables){C.RESET}"
        scrollable_list("Firewall Rules", rows, header_line=header)
        return

    # Try iptables (passwordless sudo)
    result = ssh_run("sudo -n iptables -L -n -v --line-numbers 2>/dev/null", host=host, port=port)
    if result.returncode == 0 and result.stdout.strip():
        lines = result.stdout.strip().split("\n")
        rows = [f"  {line}" for line in lines]
        header = f"  {C.BOLD}Firewall Rules (iptables){C.RESET}"
        scrollable_list("Firewall Rules", rows, header_line=header)
        return

    # Fall back to interactive TTY — allows sudo password prompt and streams output
    info("Loading firewall rules (sudo may prompt for password)...")
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend([host, "sudo iptables -L -n -v --line-numbers || sudo nft list ruleset"])
    subprocess.run(cmd)
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
