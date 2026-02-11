# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Homelab** is a Python CLI/TUI tool for managing self-hosted infrastructure. It provides interactive terminal menus (via `questionary`) for managing files over SSH/rsync and controlling homelab services via their APIs.

## Commands

```bash
# Install in development mode
pipx install -e . --force

# Run the app
homelab

# CLI flags
homelab --help
homelab --history
homelab --dashboard
homelab --dry-run
homelab --install-completions
```

Linting: `flake8 homelab/ --max-line-length=120 --ignore=E501,W503`

Import verification: `~/.local/pipx/venvs/homelab/bin/python -c "import homelab.main"`

## Architecture

The application is a Python package under `homelab/`:

```
homelab/
├── __init__.py              # Version string
├── __main__.py              # python -m homelab support
├── main.py                  # Entry point, CLI args, main menu loop, plugin registry
├── config.py                # Config loading/saving, defaults, migration from stashrc
├── keychain.py              # Fernet encryption for sensitive config values
├── sshkeys.py               # SSH key generation, viewing, deployment via ssh-copy-id
├── ui.py                    # Colors (class C), pick_option, pick_multi, confirm, prompt_text, bar_chart, sparkline, scrollable_list
├── transport.py             # ssh_run, rsync_transfer, disk space check, list_remote_*
├── files.py                 # File manager, browse, upload, download, search, trash, bookmarks
├── history.py               # Transfer history load/save/log
├── notifications.py         # Platform-aware desktop notifications + Discord webhook + clipboard
├── dashboard.py             # Status Dashboard — unified overview of all plugin stats
├── healthmonitor.py         # Health Monitor — alerts for unhealthy containers, low disk, high CPU
├── healthmap.py             # Service Health Map — ASCII topology showing service up/down status
├── watchfolder.py           # Watch Folders — monitor local dirs, auto-upload new files
├── transferqueue.py         # Transfer Queue — background batched transfers
├── quickconnect.py          # Quick Connect — unified SSH menu across all configured hosts
├── containerupdates.py      # Container Updates — compare Docker images against registry
├── auditlog.py              # Audit Log — timestamped action tracking, searchable history
├── themes.py                # Theme system — 10 preset palettes + custom hex color
├── plugins/
│   ├── __init__.py          # Plugin base class + add_plugin_favorite, add_item_favorite helpers
│   ├── unraid.py            # Dashboard, Docker (containers, compose, bulk ops, stats, resource graphs, images, system prune, update), VMs, parity check, notification center, user scripts, logs, SMART, VSCode
│   ├── dockerhost.py        # Docker Servers — multi-server Docker management (containers, compose, bulk ops, stats, resource graphs, images, system prune, update, container update checks, live logs, file browser, system stats)
│   ├── proxmox.py           # VM/LXC management, resource usage with bar charts, console access (SSH/noVNC)
│   ├── unifi.py             # Network client lookup via session auth
│   ├── opnsense.py          # Router status via HTTP Basic auth
│   ├── homeassistant.py     # Entity control, service calls, configurable dashboard view
│   ├── plex.py              # Library scan trigger
│   ├── jellyfin.py          # Library scan trigger
│   ├── sabnzbd.py           # NZB/URL download via REST API
│   ├── deluge.py            # Torrent/magnet download via JSON-RPC
│   ├── uptimekuma.py        # Monitor overview, down monitors, service health map
│   ├── npm.py               # Proxy hosts, redirections, SSL certs via token auth
│   ├── tailscale.py         # Devices, ping, exit nodes via SSH CLI
│   ├── forgejo.py           # Repos, CI runners, issues via Gitea-compatible API
│   ├── immich.py            # Library stats, jobs, uploads, albums via REST API
│   ├── syncthing.py         # Folders, devices, conflicts, system status via REST API
│   ├── arr.py               # Shared base for Sonarr/Radarr/Lidarr (Arr API)
│   ├── sonarr.py            # TV series management via Arr API
│   ├── radarr.py            # Movie management via Arr API
│   ├── lidarr.py            # Music management via Arr API
│   ├── speedtest.py         # Local speed test with history and trends
│   ├── vaultwarden.py       # Admin dashboard, user management, org stats via admin API
│   └── ansible.py           # Playbook runner, inventory viewer via SSH
├── scheduler.py             # Scheduled Tasks — cron-like recurring actions
setup.py
```

### Plugin Architecture

Each service is a plugin extending `Plugin` (in `plugins/__init__.py`):
- `is_configured()` — checks if required config fields are set
- `get_config_fields()` — returns settings for auto-generated settings menu
- `get_header_stats()` — returns cached one-line stats for the main header
- `get_health_alerts()` — returns alert strings for unhealthy states
- `get_dashboard_widgets()` — returns widget dicts for the status dashboard
- `get_menu_items()` — returns items for the main menu (only shown when configured)
- `get_actions()` — returns actions for the favorites system
- `resolve_favorite(fav)` — resolves a dict favorite back to a callable

Plugins are registered in `main.py` in the `PLUGINS` list (22 plugins total). Adding a new plugin:
1. Create `plugins/myservice.py` with a class extending `Plugin`
2. Import and add to `PLUGINS` in `main.py`
3. Add default config keys to `config.py` `DEFAULT_CONFIG`

### Core Modules

- **dashboard.py** — `status_dashboard(plugins)` shows all configured plugins' widgets in one view
- **healthmonitor.py** — `get_health_alerts(plugins)` returns alert strings shown in main menu header; checks unhealthy containers, low disk, high CPU via single SSH call
- **healthmap.py** — `health_map(plugins)` ASCII topology of all services with colored UP/DOWN/N/A indicators, grouped by layer (Infrastructure, Network, Services, Media). Accessed via Uptime Kuma menu
- **watchfolder.py** — Background thread monitors configured local directories, auto-uploads new files via rsync
- **transferqueue.py** — `enqueue(source, dest, is_dir)` public API for queuing transfers. Background worker thread processes one at a time with notifications
- **quickconnect.py** — Unified SSH menu gathering hosts from all configured plugins + custom hosts stored in `ssh_hosts` config
- **containerupdates.py** — `check_all_container_updates()` auto-discovers all Docker hosts (Unraid + docker_servers), lets user pick which to check. `_check_host(host, port)` does the actual SSH comparison of running vs registry digests.
- **auditlog.py** — `log_action(action, detail)` appends to `~/.homelab_audit.json` (max 500 entries). View recent, search, clear
- **themes.py** — 10 preset themes (default, dracula, nord, catppuccin, gruvbox, tokyo night, solarized, rose pine, monokai, ocean) + custom hex color. Drives `C.ACCENT` and questionary `Style`
- **sshkeys.py** — SSH key generation (ed25519, RSA 4096), view fingerprints, deploy to servers via ssh-copy-id
- **scheduler.py** — Cron-like scheduler for recurring tasks (speedtest, container updates, health check, config backup). Background daemon thread checks every 60s.

### Key Patterns

- `pick_option(prompt, options, header="")` — core menu, clears screen, returns index. Last option = back.
- `pick_multi(prompt, options, header="")` — checkbox multi-select, returns list of indices
- `ssh_run(cmd, capture=True, host=None, port=None)` — runs SSH command, returns CompletedProcess. **Requires explicit host.**
- TTY commands (nano, shell, logs): use `subprocess.run(["ssh", "-t", host, cmd])`
- Plugin SSH helper: Define `_ssh(command, **kwargs)` in each plugin that wraps `ssh_run(command, host=get_host(), **kwargs)`
- dockerhost.py SSH helpers: `_ssh_cmd(server, command)` and `_ssh_tty(server, command)` — pass server dict, extract host/port internally
- `questionary.select` needs `use_jk_keys=False` when `use_search_filter=True`
- Back key: Ctrl+G binding with `eager=True` (Escape removed to prevent typing hang)
- Config: `~/.homelabrc` (JSON), history: `~/.homelab_history.json`, audit: `~/.homelab_audit.json`
- Sensitive config values encrypted with Fernet (key in `~/.homelab.key`), stored with `enc:` prefix
- Config export/import: PBKDF2HMAC + Fernet password-protected bundles
- Migration: auto-copies `~/.stashrc` → `~/.homelabrc` on first run if needed
- Menu is grouped with separators: Favorites → Services → Tools → Settings → Quit
- Services only appear in menu when their config is set
- Header stats are plugin-driven with 5-minute TTL caching, fetched in background threads
- Thread-local output suppression via `threading.local()` for background threads
- Session restore: saves last selected menu key to config, offers to resume on next launch
- `build_main_menu()` returns `(menu_items, actions, action_keys)` — action_keys used for session restore
- API plugins use `urllib.request` with authenticated HTTP calls, each with internal `_api()` helper
- Background features (watch folder, transfer queue, header refresh) use daemon threads with `threading.Event` for stop signals
- Docker format strings in SSH commands need `{{{{}}}}` quadruple braces in Python f-strings
- File functions require: `host`, `base_path`, optional `extra_paths`, `trash_path`
- `manage_bookmarks()` and `show_history()` are local-only, no host needed
- Core modules (files.py, transport.py, healthmonitor.py, containerupdates.py, watchfolder.py, transferqueue.py) are **generic** — they take `host`, `base_path`, etc. as parameters. Plugins pass their specific config values when calling them.

### Docker Feature Parity

The `dockerhost.py` and `unraid.py` Docker features should stay aligned. Both support:
- Container management (start/stop/restart/shell/logs/inspect/update)
- Compose projects (up/down/pull & up/edit/validate/restart service)
- Bulk operations (select or all: start/stop/restart)
- Docker stats (CPU, RAM, network per container)
- Resource graphs (sparkline trends from multiple samples)
- Image management (list, remove, prune dangling, prune all)
- System prune (basic + full with disk usage display)
- Container update (pull latest image + restart)
- Live logs (follow, static view, search, multi-container combined)

Unraid-specific extras: VMs, parity check, user scripts, notification center, SMART, appdata browser, VSCode, syslog

## Entry Point

`setup.py` defines console script `homelab` → `homelab.main:main()`
