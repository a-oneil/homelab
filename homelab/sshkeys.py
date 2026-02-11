"""SSH Key Management — generate, view, and deploy SSH key pairs."""

import glob
import os
import subprocess

from homelab.auditlog import log_action
from homelab.config import CFG
from homelab.ui import (
    C, pick_option, confirm, prompt_text, scrollable_list,
    info, success, error, warn,
)


def ssh_key_menu():
    """Main SSH key management menu."""
    while True:
        idx = pick_option("SSH Key Management:", [
            "Deploy Key to Server  — copy public key to a remote host",
            "Generate Key Pair     — create new ed25519 or RSA key",
            "View Keys             — list keys in ~/.ssh/",
            "───────────────",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 3:
            continue
        elif idx == 0:
            _deploy_key()
        elif idx == 1:
            _generate_key()
        elif idx == 2:
            _view_keys()


def _get_pub_keys():
    """Return list of public key file paths in ~/.ssh/."""
    ssh_dir = os.path.expanduser("~/.ssh")
    return sorted(glob.glob(os.path.join(ssh_dir, "*.pub")))


def _view_keys():
    """List SSH keys with fingerprints."""
    pub_keys = _get_pub_keys()
    if not pub_keys:
        warn("No SSH public keys found in ~/.ssh/")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for pub in pub_keys:
        filename = os.path.basename(pub)
        # Get fingerprint
        try:
            result = subprocess.run(
                ["ssh-keygen", "-lf", pub],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                # Output: 256 SHA256:... comment (ED25519)
                parts = result.stdout.strip().split()
                bits = parts[0] if parts else "?"
                fingerprint = parts[1] if len(parts) > 1 else "?"
                key_type = parts[-1].strip("()") if parts else "?"
                comment = " ".join(parts[2:-1]) if len(parts) > 3 else ""
                rows.append(f"{filename:<25} {key_type:<10} {bits}b  {fingerprint}")
                if comment:
                    rows.append(f"  {C.DIM}{comment}{C.RESET}")
            else:
                rows.append(f"{filename:<25} (could not read)")
        except (subprocess.TimeoutExpired, OSError):
            rows.append(f"{filename:<25} (error reading key)")

    scrollable_list("SSH Keys", rows,
                    header_line=f"\n  {C.BOLD}SSH Keys{C.RESET} ({len(pub_keys)} found)\n")


def _generate_key():
    """Generate a new SSH key pair."""
    idx = pick_option("Key type:", [
        "ed25519 (recommended)",
        "RSA 4096",
        "← Back",
    ])
    if idx == 2:
        return

    key_type = "ed25519" if idx == 0 else "rsa"
    default_name = "id_ed25519" if idx == 0 else "id_rsa"

    key_name = prompt_text("Key filename (in ~/.ssh/):", default=default_name)
    if not key_name:
        return

    ssh_dir = os.path.expanduser("~/.ssh")
    key_path = os.path.join(ssh_dir, key_name)

    if os.path.exists(key_path):
        if not confirm(f"{key_name} already exists. Overwrite?", default_yes=False):
            return

    passphrase = prompt_text("Passphrase (leave blank for none):")

    # Build comment from username@hostname
    import getpass
    import platform
    comment = f"{getpass.getuser()}@{platform.node()}"

    # Ensure ~/.ssh exists
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

    cmd = ["ssh-keygen", "-t", key_type, "-f", key_path, "-N", passphrase, "-C", comment]
    if key_type == "rsa":
        cmd.extend(["-b", "4096"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            log_action("SSH Key Generate", f"{key_type} ({key_name})")
            success(f"Key pair generated: {key_path}")
            # Show fingerprint
            fp = subprocess.run(["ssh-keygen", "-lf", f"{key_path}.pub"],
                                capture_output=True, text=True, timeout=5)
            if fp.returncode == 0:
                info(f"Fingerprint: {fp.stdout.strip()}")
        else:
            error(f"Key generation failed: {result.stderr.strip()[:100]}")
    except subprocess.TimeoutExpired:
        error("Key generation timed out.")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _gather_deploy_hosts():
    """Collect all known SSH hosts for key deployment."""
    hosts = []
    unraid = CFG.get("unraid_ssh_host", "")
    if unraid:
        hosts.append({"name": "Unraid", "host": unraid})
    proxmox_host = CFG.get("proxmox_ssh_host", "")
    if not proxmox_host:
        url = CFG.get("proxmox_url", "")
        if url:
            proxmox_host = "root@" + url.replace("https://", "").replace("http://", "").split(":")[0]
    if proxmox_host:
        hosts.append({"name": "Proxmox", "host": proxmox_host})
    ha_host = CFG.get("homeassistant_ssh_host", "")
    if ha_host:
        port = str(CFG.get("homeassistant_ssh_port", "")).strip()
        hosts.append({"name": "Home Assistant", "host": ha_host, "port": port})
    for s in CFG.get("docker_servers", []):
        hosts.append({
            "name": s.get("name", "?"), "host": s.get("host", ""),
            "port": s.get("port", ""),
        })
    for custom in CFG.get("ssh_hosts", []):
        hosts.append(custom)
    return hosts


def _deploy_key():
    """Deploy a public key to a remote server via ssh-copy-id."""
    pub_keys = _get_pub_keys()
    if not pub_keys:
        warn("No SSH public keys found. Generate one first.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Pick key
    key_choices = [os.path.basename(k) for k in pub_keys]
    key_choices.append("← Back")
    kidx = pick_option("Deploy which key?", key_choices)
    if kidx >= len(pub_keys):
        return
    selected_key = pub_keys[kidx]

    # Pick host
    hosts = _gather_deploy_hosts()
    if not hosts:
        warn("No SSH hosts configured.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    host_choices = []
    for h in hosts:
        port = h.get("port", "")
        port_str = f" :{port}" if port else ""
        host_choices.append(f"{h.get('name', '?'):<20} {C.DIM}{h['host']}{port_str}{C.RESET}")
    host_choices.append("Enter manually")
    host_choices.append("← Back")

    hidx = pick_option("Deploy to:", host_choices)
    if hidx == len(hosts) + 1:  # Back
        return
    elif hidx == len(hosts):  # Manual
        manual_host = prompt_text("SSH host (e.g. user@10.0.0.5):")
        if not manual_host:
            return
        target = {"name": manual_host, "host": manual_host}
    else:
        target = hosts[hidx]

    host = target["host"]
    port = target.get("port", "")
    name = target.get("name", host)

    info(f"Deploying {os.path.basename(selected_key)} to {name}...")
    cmd = ["ssh-copy-id", "-i", selected_key]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.append(host)

    result = subprocess.run(cmd, timeout=30)
    if result.returncode == 0:
        log_action("SSH Key Deploy", f"{os.path.basename(selected_key)} → {name} ({host})")
        success(f"Key deployed to {name}")
        # Test connection
        info("Testing key-based connection...")
        test_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if port:
            test_cmd.extend(["-p", str(port)])
        test_cmd.extend([host, "echo ok"])
        test = subprocess.run(test_cmd, capture_output=True, text=True,
                              timeout=10, stdin=subprocess.DEVNULL)
        if test.returncode == 0:
            success("Key-based authentication working!")
        else:
            warn("Connection test failed — key may not have been deployed correctly.")
    else:
        error("Key deployment failed.")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
