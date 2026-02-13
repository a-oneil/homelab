"""Ansible plugin — manage playbooks, inventory, vault, and ad-hoc commands via SSH."""

import subprocess
import time

from homelab.modules.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.modules.transport import ssh_run
from homelab.ui import (
    C, pick_option, confirm, prompt_text, scrollable_list,
    info, warn,
)

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _get_host():
    return CFG.get("ansible_ssh_host", "")


def _ssh(command, **kwargs):
    return ssh_run(command, host=_get_host(), **kwargs)


def _get_playbook_path():
    return CFG.get("ansible_playbook_path", "").rstrip("/")


def _get_inventory_path():
    return CFG.get("ansible_inventory_path", "")


class AnsiblePlugin(Plugin):
    name = "Ansible"
    key = "ansible"

    def is_configured(self):
        return bool(CFG.get("ansible_ssh_host") and CFG.get("ansible_playbook_path"))

    def get_config_fields(self):
        return [
            ("ansible_ssh_host", "Ansible SSH Host", "e.g. root@10.0.0.5", False),
            ("ansible_playbook_path", "Ansible Playbook Path", "e.g. /opt/ansible", False),
            ("ansible_inventory_path", "Ansible Inventory Path", "e.g. /opt/ansible/inventory", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        path = _get_playbook_path()
        if not path:
            return []
        result = _ssh(
            f"find '{path}' -maxdepth 2 \\( -name '*.yml' -o -name '*.yaml' \\)"
            f" | grep -v roles/ | wc -l"
        )
        if result.returncode != 0:
            return []
        count = result.stdout.strip()
        return [{"title": "Ansible", "lines": [
            f"{count} playbooks",
            f"{C.DIM}{path}{C.RESET}",
        ]}]

    def get_menu_items(self):
        return [
            ("Ansible              — playbooks, inventory, ad-hoc commands", ansible_menu),
        ]

    def get_actions(self):
        return {
            "Ansible Playbooks": ("ansible_playbooks", _playbooks_menu),
            "Ansible Inventory": ("ansible_inventory", _inventory_menu),
            "Ansible Ad-hoc": ("ansible_adhoc", _adhoc_menu),
        }


# ─── Stats ────────────────────────────────────────────────────────────────


def _fetch_stats():
    path = _get_playbook_path()
    if not path:
        _HEADER_CACHE["timestamp"] = time.time()
        return
    result = _ssh(
        f"find '{path}' -maxdepth 2 \\( -name '*.yml' -o -name '*.yaml' \\)"
        f" | grep -v roles/ | wc -l",
        background=True,
    )
    if result.returncode == 0:
        count = result.stdout.strip()
        _HEADER_CACHE["stats"] = f"Ansible: {count} playbooks at {path}"
    _HEADER_CACHE["timestamp"] = time.time()


# ─── Main Menu ────────────────────────────────────────────────────────────


def ansible_menu():
    while True:
        idx = pick_option("Ansible:", [
            "Playbooks            — list and run playbooks",
            "Inventory            — view hosts and groups",
            "Ad-hoc Command       — run commands on hosts",
            "Roles                — list installed roles",
            "Vault                — manage encrypted files",
            "Galaxy               — installed collections",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 8:
            return
        elif idx == 6:
            continue
        elif idx == 7:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(AnsiblePlugin())
        elif idx == 0:
            _playbooks_menu()
        elif idx == 1:
            _inventory_menu()
        elif idx == 2:
            _adhoc_menu()
        elif idx == 3:
            _roles_menu()
        elif idx == 4:
            _vault_menu()
        elif idx == 5:
            _galaxy_menu()


# ─── Playbooks ────────────────────────────────────────────────────────────


def _playbooks_menu():
    path = _get_playbook_path()
    if not path:
        warn("Playbook path not configured.")
        return

    while True:
        result = _ssh(
            f"find '{path}' -maxdepth 2 \\( -name '*.yml' -o -name '*.yaml' \\)"
            f" | grep -v roles/ | sort"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No playbooks found.")
            return

        playbooks = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        # Show relative names for readability
        display = []
        for pb in playbooks:
            rel = pb.replace(path + "/", "") if pb.startswith(path) else pb
            display.append(rel)

        choices = list(display)
        choices.append("← Back")
        idx = pick_option("Playbooks:", choices)
        if idx >= len(playbooks):
            return

        _playbook_detail(playbooks[idx], display[idx])


def _playbook_detail(playbook_path, display_name):
    """Show playbook content preview and run options."""
    path = _get_playbook_path()
    inv = _get_inventory_path()

    # Show first 30 lines
    preview = _ssh(f"head -30 '{playbook_path}'")
    if preview.returncode == 0 and preview.stdout.strip():
        print(f"\n  {C.BOLD}{display_name}{C.RESET}\n")
        for line in preview.stdout.splitlines():
            print(f"  {C.DIM}{line}{C.RESET}")
        print()

    inv_flag = f" -i '{inv}'" if inv else ""
    choices = [
        "Run playbook",
        "Dry run (--check)",
        "View full file",
        "← Back",
    ]
    idx = pick_option(f"Playbook: {display_name}", choices)
    if idx == 3:
        return
    elif idx == 0:
        if confirm(f"Run {display_name}?"):
            log_action("Ansible Run Playbook", display_name)
            info(f"Running {display_name}...")
            subprocess.run(
                ["ssh", "-t", _get_host(), f"cd '{path}' && ansible-playbook{inv_flag} '{playbook_path}'"]
            )
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif idx == 1:
        if confirm(f"Dry-run {display_name}?"):
            log_action("Ansible Dry Run", display_name)
            info(f"Dry-running {display_name} (--check)...")
            subprocess.run(
                ["ssh", "-t", _get_host(), f"cd '{path}' && ansible-playbook{inv_flag} --check '{playbook_path}'"]
            )
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif idx == 2:
        full = _ssh(f"cat '{playbook_path}'")
        if full.returncode == 0 and full.stdout.strip():
            scrollable_list(f"Playbook: {display_name}", full.stdout.splitlines())
        else:
            warn("Could not read playbook file.")


# ─── Inventory ────────────────────────────────────────────────────────────


def _inventory_menu():
    inv = _get_inventory_path()
    if not inv:
        warn("Inventory path not configured.")
        return

    while True:
        idx = pick_option("Inventory:", [
            "Graph view           — tree of hosts and groups",
            "List view            — detailed YAML inventory",
            "← Back",
        ])
        if idx == 2:
            return
        elif idx == 0:
            _inventory_graph()
        elif idx == 1:
            _inventory_list()


def _inventory_graph():
    """Show inventory as a tree graph."""
    inv = _get_inventory_path()
    result = _ssh(f"ansible-inventory -i '{inv}' --graph 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("Could not retrieve inventory graph.")
        return

    lines = result.stdout.strip().splitlines()
    scrollable_list("Inventory Graph:", lines)


def _inventory_list():
    """Show inventory in detailed YAML format."""
    inv = _get_inventory_path()
    result = _ssh(f"ansible-inventory -i '{inv}' --list --yaml 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("Could not retrieve inventory.")
        return

    lines = result.stdout.strip().splitlines()
    scrollable_list("Inventory (YAML):", lines)


# ─── Ad-hoc Command ──────────────────────────────────────────────────────


def _adhoc_menu():
    inv = _get_inventory_path()
    inv_flag = f" -i '{inv}'" if inv else ""

    pattern = prompt_text("Host pattern:", default="all")
    if not pattern:
        return

    module = prompt_text("Module:", default="ping")
    if not module:
        return

    args = ""
    if module != "ping":
        args = prompt_text("Module arguments:", default="")

    args_flag = f" -a '{args}'" if args else ""
    cmd = f"ansible {pattern}{inv_flag} -m {module}{args_flag}"

    print(f"\n  {C.DIM}$ {cmd}{C.RESET}\n")
    if confirm("Run this command?"):
        log_action("Ansible Ad-hoc", f"{module} on {pattern}")
        subprocess.run(["ssh", "-t", _get_host(), cmd])
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Roles ────────────────────────────────────────────────────────────────


def _roles_menu():
    path = _get_playbook_path()
    if not path:
        warn("Playbook path not configured.")
        return

    result = _ssh(f"ls -1 '{path}/roles/' 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("No roles found or roles/ directory does not exist.")
        return

    roles = [r.strip() for r in result.stdout.strip().splitlines() if r.strip()]
    if not roles:
        warn("No roles found.")
        return

    rows = []
    for role in roles:
        # Check for main.yml to show a brief description
        meta = _ssh(f"head -5 '{path}/roles/{role}/meta/main.yml' 2>/dev/null")
        desc = ""
        if meta.returncode == 0 and meta.stdout.strip():
            for line in meta.stdout.splitlines():
                if "description" in line.lower():
                    desc = line.split(":", 1)[-1].strip().strip("'\"")
                    break
        if desc:
            rows.append(f"{role:<30} {C.DIM}{desc}{C.RESET}")
        else:
            rows.append(role)

    scrollable_list(f"Roles ({len(roles)}):", rows)


# ─── Vault ────────────────────────────────────────────────────────────────


def _vault_menu():
    path = _get_playbook_path()
    if not path:
        warn("Playbook path not configured.")
        return

    while True:
        idx = pick_option("Vault:", [
            "View encrypted file  — decrypt and display",
            "Edit encrypted file  — open in editor",
            "Encrypt a file       — encrypt an existing file",
            "Decrypt a file       — decrypt in place",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 0:
            _vault_view()
        elif idx == 1:
            _vault_edit()
        elif idx == 2:
            _vault_encrypt()
        elif idx == 3:
            _vault_decrypt()


def _vault_list_encrypted():
    """Return list of vault-encrypted files."""
    path = _get_playbook_path()
    result = _ssh(f"grep -rl '\\$ANSIBLE_VAULT' '{path}' 2>/dev/null | head -20")
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def _vault_pick_file():
    """Let user pick from encrypted files."""
    files = _vault_list_encrypted()
    if not files:
        warn("No vault-encrypted files found.")
        return None

    path = _get_playbook_path()
    display = []
    for f in files:
        rel = f.replace(path + "/", "") if f.startswith(path) else f
        display.append(rel)

    choices = list(display)
    choices.append("← Back")
    idx = pick_option("Encrypted files:", choices)
    if idx >= len(files):
        return None
    return files[idx]


def _vault_view():
    """View a vault-encrypted file (TTY for password prompt)."""
    vault_file = _vault_pick_file()
    if not vault_file:
        return
    log_action("Ansible Vault View", vault_file)
    subprocess.run(["ssh", "-t", _get_host(), f"ansible-vault view '{vault_file}'"])
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _vault_edit():
    """Edit a vault-encrypted file (TTY for editor + password)."""
    vault_file = _vault_pick_file()
    if not vault_file:
        return
    log_action("Ansible Vault Edit", vault_file)
    subprocess.run(["ssh", "-t", _get_host(), f"ansible-vault edit '{vault_file}'"])


def _vault_encrypt():
    """Encrypt an unencrypted file with ansible-vault."""
    path = _get_playbook_path()
    # List non-encrypted yml files
    result = _ssh(
        f"find '{path}' -maxdepth 3 \\( -name '*.yml' -o -name '*.yaml' \\) -exec"
        f" grep -rL '\\$ANSIBLE_VAULT' {{}} + 2>/dev/null | head -30"
    )
    if result.returncode != 0 or not result.stdout.strip():
        warn("No unencrypted files found.")
        return

    files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    display = []
    for f in files:
        rel = f.replace(path + "/", "") if f.startswith(path) else f
        display.append(rel)

    choices = list(display)
    choices.append("← Back")
    idx = pick_option("Encrypt which file?", choices)
    if idx >= len(files):
        return

    target = files[idx]
    if confirm(f"Encrypt {display[idx]}?"):
        log_action("Ansible Vault Encrypt", target)
        subprocess.run(["ssh", "-t", _get_host(), f"ansible-vault encrypt '{target}'"])
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _vault_decrypt():
    """Decrypt a vault-encrypted file in place."""
    vault_file = _vault_pick_file()
    if not vault_file:
        return
    path = _get_playbook_path()
    display = vault_file.replace(path + "/", "") if vault_file.startswith(path) else vault_file
    if confirm(f"Decrypt {display} in place?"):
        log_action("Ansible Vault Decrypt", vault_file)
        subprocess.run(["ssh", "-t", _get_host(), f"ansible-vault decrypt '{vault_file}'"])
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Galaxy ───────────────────────────────────────────────────────────────


def _galaxy_menu():
    result = _ssh("ansible-galaxy collection list 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("No collections found or ansible-galaxy not available.")
        return

    lines = result.stdout.strip().splitlines()
    scrollable_list("Galaxy Collections:", lines)
