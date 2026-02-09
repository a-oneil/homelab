# Homelab

**Self-Hosted Infrastructure Manager** — a terminal UI for managing your homelab services.

Browse and transfer files over SSH, manage Docker containers, control VMs, monitor network clients, and interact with your smart home — all from one interactive CLI.

## Features

### File Management
- **File Manager** — browse, rename, move, copy, delete, preview, edit files on your server
- **Transfer to Server** — download from web (wget, curl, git, yt-dlp) and send to server
- **Upload Local Files** — send files from your computer via rsync
- **Download from Server** — pull files to your local machine, with compress & download option
- **Search** — find files by name, content (grep), or file type within any folder
- **Find Duplicates** — scan for duplicate files by size + partial hash
- **Multi-select** — batch download, delete, move, or compress multiple items
- **Batch Rename** — regex-based renaming across files in a directory
- **Bookmarks** — save and jump to frequently used folders
- **Trash** — soft-delete with restore (sidecar `.origin` files track original paths)

### Service Plugins (19)
- **Unraid** — dashboard (disk usage, CPU, RAM, SMART), Docker management (stats, inspect, update, images, compose), VMs (virsh), parity check, notification center, user scripts, logs
- **Proxmox** — QEMU VMs and LXC containers (start, stop, reboot, snapshot), resource usage with bar charts, console access (SSH/noVNC)
- **UniFi** — view active network clients, devices, and search by hostname or MAC
- **OPNsense** — system status, interface info, ARP table, DHCP leases
- **Home Assistant** — browse entities by domain, view states/attributes, toggle switches/lights, call services, configurable dashboard view
- **Uptime Kuma** — monitor overview with status/ping/uptime, filter to down monitors, **service health map** (ASCII topology of all services with live up/down status)
- **Nginx Proxy Manager** — list/add/edit proxy hosts, redirections, SSL certificates
- **Tailscale** — list devices, ping, manage exit nodes (via SSH to server)
- **Forgejo** — repositories, CI runners, issues (Gitea-compatible API)
- **Immich** — library stats, server info, job management, recent uploads, albums
- **Syncthing** — folder sync status, connected devices, conflicts, system status
- **Sonarr** — TV series, calendar, queue, wanted episodes
- **Radarr** — movies, calendar, queue, wanted films
- **Lidarr** — music artists, albums, wanted tracks
- **Plex** — library scan trigger
- **Jellyfin** — library scan trigger
- **SABnzbd** — send NZBs and URLs
- **Deluge** — send torrents and magnet links
- **Speedtest** — local speed test with history tracking and trend charts

### Tools
- **Status Dashboard** — unified overview pulling widgets from all configured plugins
- **Quick Connect** — SSH into any configured host (Unraid, Proxmox, HA + custom hosts you add)
- **Container Updates** — compare running Docker image digests against the registry, shows what's outdated
- **Health Monitor** — alerts for unhealthy containers, low disk, high CPU shown in the main menu header
- **Watch Folders** — monitor local directories and auto-upload new files to the server
- **Transfer Queue** — background batched transfers with worker thread and notifications
- **Audit Log** — timestamped action history with search (stored in `~/.homelab_audit.json`)
- **Session Restore** — remembers your last menu selection, offers to resume on next launch
- **Download Client** — send URLs/magnets to SABnzbd or Deluge, upload .nzb/.torrent files
- **Media Scan** — trigger Plex or Jellyfin library scans
- **Open in VSCode** — launch VSCode Remote-SSH to your server workspace
- **History** — view and re-run past transfers
- **Favorites** — pin frequently used actions to the top of the menu

### Customization
- **Themes** — 10 preset color palettes (default, dracula, nord, catppuccin, gruvbox, tokyo night, solarized, rose pine, monokai, ocean) + custom hex color
- **Config Encryption** — sensitive values (API keys, passwords) encrypted with Fernet
- **Export/Import Config** — password-protected encrypted backup for migration between machines

## Installation

### With pipx (recommended)
```bash
pipx install git+https://github.com/yourusername/homelab.git
```

### With pip
```bash
pip install git+https://github.com/yourusername/homelab.git
```

### From source
```bash
git clone https://github.com/yourusername/homelab.git
cd homelab
pip install -e .
```

## First Run

```bash
homelab
```

On first run, Homelab creates `~/.homelabrc` with default settings. Go to **Settings** to configure:

1. **SSH Host** — your server's SSH address (e.g. `root@10.20.0.2`)
2. **Remote Base** — the base path for file browsing (e.g. `/mnt/user`)
3. **Service credentials** — configure any services you want to use

### SSH Setup
Homelab uses SSH key-based authentication. Make sure you can connect without a password:
```bash
ssh-copy-id root@your-server-ip
```

### Migrating from Stash
If you previously used Stash, Homelab automatically copies `~/.stashrc` to `~/.homelabrc` on first run. No manual migration needed.

## Plugin Configuration

### Unraid
Set SSH Host and Remote Base in Settings. Optionally configure:
- Unraid API URL + Key for GraphQL access
- VSCode workspace path and SSH host alias
- Trash path for soft-delete

### Proxmox
- **URL**: `https://your-proxmox-ip:8006`
- **API Token**: Create in Proxmox → Datacenter → API Tokens (format: `user@realm!tokenname=uuid`)
- **Node**: Your Proxmox node name (e.g. `pve`)

### UniFi
- **URL**: `https://your-controller-ip:8443`
- **Username/Password**: Your UniFi controller credentials
- **Site**: Usually `default`

### OPNsense
- **URL**: `https://your-opnsense-ip`
- **API Key/Secret**: Create in OPNsense → System → Access → Users → API Keys

### Home Assistant
- **URL**: `http://your-ha-ip:8123`
- **Token**: Create a Long-Lived Access Token in HA → Profile → Security

### Uptime Kuma
- **URL**: `http://your-kuma-ip:3001`
- **Username/Password**: Your Uptime Kuma login credentials

### Nginx Proxy Manager
- **URL**: `http://your-npm-ip:81`
- **Email/Password**: Your NPM admin credentials

### Tailscale
- Enable in Settings — runs `tailscale` CLI via SSH on your server

### Forgejo
- **URL**: `https://your-forgejo-instance`
- **Token**: Create in Forgejo → Settings → Applications → Access Tokens

### Immich
- **URL**: `http://your-immich-ip:2283`
- **API Key**: Create in Immich → Account Settings → API Keys

### Syncthing
- **URL**: `http://your-syncthing-ip:8384`
- **API Key**: Found in Syncthing → Settings → GUI → API Key

### Sonarr / Radarr / Lidarr
- **URL**: `http://your-instance-ip:port`
- **API Key**: Found in Settings → General → API Key

### Media Servers
- **Plex**: URL + X-Plex-Token
- **Jellyfin**: URL + API Key

### Download Clients
- **SABnzbd**: URL + API Key
- **Deluge**: URL + Web UI Password

## CLI Flags

```
homelab                      Interactive mode
homelab --history            View transfer history
homelab --dashboard          Server dashboard
homelab --dry-run            Toggle dry run mode
homelab --install-completions Install shell completions
homelab --help               Show this help
```

## Cross-Platform

Homelab works on macOS and Linux:
- Desktop notifications: `osascript` (macOS), `notify-send` (Linux)
- Clipboard: `pbcopy` (macOS), `xclip` (Linux), `clip` (Windows)
- Hostname: `scutil --get ComputerName` (macOS), `platform.node()` (Linux)
- All local paths use `os.path.expanduser("~")` — no hardcoded paths

## Adding a Plugin

1. Create `homelab/plugins/myservice.py`
2. Define a class extending `Plugin` with required methods
3. Import and add to `PLUGINS` in `homelab/main.py`
4. Add default config keys to `DEFAULT_CONFIG` in `homelab/config.py`

The plugin's config fields auto-appear in Settings, and menu items auto-appear when configured.

## License

MIT

---

Built with [Claude Code](https://claude.ai/code)
