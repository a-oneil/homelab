"""Configuration loading, saving, defaults, and migration from stashrc."""

import json
import os
import platform
import shutil
import subprocess

from homelab import keychain

CONFIG_PATH = os.path.expanduser("~/.homelabrc")
HISTORY_PATH = os.path.expanduser("~/.homelab_history.json")

_ENC_PREFIX = "enc:"

# Config keys that contain secrets (passwords, tokens, API keys)
_SENSITIVE_KEYS = {
    "unraid_api_key", "plex_token", "jellyfin_token",
    "sabnzbd_api_key", "deluge_password",
    "proxmox_api_token", "unifi_password",
    "opnsense_api_key", "opnsense_api_secret",
    "homeassistant_token", "uptimekuma_password",
    "npm_password", "forgejo_token", "github_token",
    "immich_api_key", "syncthing_api_key",
    "sonarr_api_key", "radarr_api_key", "lidarr_api_key",
    "discord_webhook_url",
}

# Legacy paths for migration
_LEGACY_CONFIG = os.path.expanduser("~/.stashrc")
_LEGACY_HISTORY = os.path.expanduser("~/.stash_history.json")

DEFAULT_CONFIG = {
    # Core
    "default_download_dir": "~/Downloads",
    "bookmarks": [],
    "favorites": [],
    "notifications": True,
    "dry_run": False,
    "accent_color": "#bd93f9",
    "disk_space_warn_gb": 5,
    "discord_webhook_url": "",
    # Unraid plugin
    "unraid_ssh_host": "",
    "unraid_remote_base": "/mnt/user",
    "unraid_trash_path": "/mnt/user/.homelab_trash",
    "unraid_extra_paths": [
        "/boot/config/plugins/compose.manager/projects",
        "/boot/config/plugins/user.scripts",
    ],
    "unraid_vscode_workspace": "/boot/vscode.code-workspace",
    "unraid_vscode_ssh_host": "unraid",
    "unraid_api_url": "",
    "unraid_api_key": "",
    # Plex plugin
    "plex_url": "",
    "plex_token": "",
    # Jellyfin plugin
    "jellyfin_url": "",
    "jellyfin_token": "",
    # SABnzbd plugin
    "sabnzbd_url": "",
    "sabnzbd_api_key": "",
    # Deluge plugin
    "deluge_url": "",
    "deluge_password": "",
    # Proxmox plugin
    "proxmox_url": "",
    "proxmox_api_token": "",
    "proxmox_node": "",
    "proxmox_ssh_host": "",
    # UniFi plugin
    "unifi_url": "",
    "unifi_username": "",
    "unifi_password": "",
    "unifi_site": "default",
    # OPNsense plugin
    "opnsense_url": "",
    "opnsense_api_key": "",
    "opnsense_api_secret": "",
    # Home Assistant plugin
    "homeassistant_url": "",
    "homeassistant_token": "",
    "homeassistant_ssh_host": "",
    "homeassistant_ssh_port": "",
    "ha_dashboard_entities": [],
    # Uptime Kuma plugin
    "uptimekuma_url": "",
    "uptimekuma_username": "",
    "uptimekuma_password": "",
    # Nginx Proxy Manager plugin
    "npm_url": "",
    "npm_email": "",
    "npm_password": "",
    # Tailscale plugin
    "tailscale_enabled": False,
    # Forgejo plugin
    "forgejo_url": "",
    "forgejo_token": "",
    # GitHub plugin
    "github_token": "",
    # Immich plugin
    "immich_url": "",
    "immich_api_key": "",
    # Syncthing plugin
    "syncthing_url": "",
    "syncthing_api_key": "",
    # Sonarr plugin
    "sonarr_url": "",
    "sonarr_api_key": "",
    # Radarr plugin
    "radarr_url": "",
    "radarr_api_key": "",
    # Lidarr plugin
    "lidarr_url": "",
    "lidarr_api_key": "",
    # Ansible plugin
    "ansible_ssh_host": "",
    "ansible_playbook_path": "",
    "ansible_inventory_path": "",
    # Speedtest
    "speedtest_history": [],
    # Watch folders
    "watch_folders": [],
    # Scheduled tasks
    "scheduled_tasks": [],
    # Session restore
    "session_last_menu": "",
    # Theme
    "theme": "default",
    # Quick Connect custom SSH hosts
    "ssh_hosts": [],
    # Generic Docker/Linux servers
    "docker_servers": [],
}

# Key renames from stashrc -> homelabrc
_KEY_MIGRATION = {
    "trash_path": "unraid_trash_path",
    "extra_paths": "unraid_extra_paths",
    "vscode_workspace": "unraid_vscode_workspace",
    "vscode_ssh_host": "unraid_vscode_ssh_host",
    "ssh_host": "unraid_ssh_host",
    "remote_base": "unraid_remote_base",
}


def _migrate_legacy():
    """Migrate from stashrc/stash_history if they exist and homelabrc does not."""
    if os.path.exists(CONFIG_PATH):
        return
    if os.path.exists(_LEGACY_CONFIG):
        shutil.copy2(_LEGACY_CONFIG, CONFIG_PATH)
    if not os.path.exists(HISTORY_PATH) and os.path.exists(_LEGACY_HISTORY):
        shutil.copy2(_LEGACY_HISTORY, HISTORY_PATH)


def load_config():
    _migrate_legacy()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        # Migrate renamed keys
        for old_key, new_key in _KEY_MIGRATION.items():
            if old_key in cfg and new_key not in cfg:
                cfg[new_key] = cfg.pop(old_key)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        # Decrypt secrets
        if keychain.is_available():
            migrated = False
            for key in _SENSITIVE_KEYS:
                val = cfg.get(key, "")
                if isinstance(val, str) and val.startswith(_ENC_PREFIX):
                    # Decrypt encrypted value
                    plaintext = keychain.retrieve(key, val[len(_ENC_PREFIX):])
                    cfg[key] = plaintext if plaintext is not None else ""
                elif val and not val.startswith(_ENC_PREFIX):
                    # Migrate plaintext secret → encrypted on first load
                    encrypted = keychain.store(key, val)
                    if encrypted:
                        cfg[key] = val  # Keep plaintext in memory
                        migrated = True
            if migrated:
                _save_encrypted(cfg)
        return cfg
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def _save_encrypted(cfg):
    """Write config to disk with sensitive values encrypted."""
    disk_cfg = dict(cfg)
    if keychain.is_available():
        for key in _SENSITIVE_KEYS:
            val = disk_cfg.get(key, "")
            if val and not val.startswith(_ENC_PREFIX):
                encrypted = keychain.store(key, val)
                if encrypted:
                    disk_cfg[key] = _ENC_PREFIX + encrypted
    with open(CONFIG_PATH, "w") as f:
        json.dump(disk_cfg, f, indent=2)


def save_config(cfg):
    """Save config, encrypting sensitive values if possible."""
    _save_encrypted(cfg)


def local_hostname():
    """Return the local machine's hostname for display."""
    override = CFG.get("hostname", "")
    if override:
        return override
    # On macOS, prefer the user-set ComputerName over the network hostname
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["scutil", "--get", "ComputerName"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return platform.node() or "this computer"


CFG = load_config()
