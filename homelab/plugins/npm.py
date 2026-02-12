"""Nginx Proxy Manager plugin — list/add/edit proxy hosts via API."""

import json
import re
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.modules.auditlog import log_action
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, pick_multi, confirm, prompt_text, success, error, warn, info

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300
_TOKEN_CACHE = {"token": "", "expires": 0}


def _get_token():
    """Get or refresh an API token."""
    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires"]:
        return _TOKEN_CACHE["token"]

    base = CFG.get("npm_url", "").rstrip("/")
    email = CFG.get("npm_email", "")
    password = CFG.get("npm_password", "")
    if not base or not email or not password:
        return None

    payload = json.dumps({"identity": email, "secret": password}).encode()
    req = urllib.request.Request(
        f"{base}/api/tokens",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            _TOKEN_CACHE["token"] = data.get("token", "")
            _TOKEN_CACHE["expires"] = time.time() + 3500  # ~1hr
            return _TOKEN_CACHE["token"]
    except Exception as e:
        error(f"NPM auth error: {e}")
        return None


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to NPM."""
    base = CFG.get("npm_url", "").rstrip("/")
    token = _get_token()
    if not base or not token:
        return None

    url = f"{base}/api{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"NPM API error: {e}")
        return None


class NpmPlugin(Plugin):
    name = "Nginx Proxy Manager"
    key = "npm"

    def is_configured(self):
        return bool(CFG.get("npm_url") and CFG.get("npm_email") and CFG.get("npm_password"))

    def get_config_fields(self):
        return [
            ("npm_url", "NPM URL", "e.g. http://192.168.1.100:81", False),
            ("npm_email", "NPM Email", "admin login email", False),
            ("npm_password", "NPM Password", "admin login password", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        hosts = _api("/nginx/proxy-hosts")
        certs = _api("/nginx/certificates")
        lines = []
        if hosts and isinstance(hosts, list):
            enabled = sum(1 for h in hosts if h.get("enabled"))
            lines.append(f"{len(hosts)} proxy hosts ({enabled} enabled)")
            ssl_count = sum(1 for h in hosts if h.get("certificate_id"))
            lines.append(f"{ssl_count} with SSL")
        if certs and isinstance(certs, list):
            lines.append(f"{len(certs)} certificates")
        if not lines:
            return []
        return [{"title": "Nginx Proxy Manager", "lines": lines}]

    def get_menu_items(self):
        return [
            ("Nginx Proxy Manager  — proxy hosts, SSL, redirects", npm_menu),
        ]

    def get_actions(self):
        return {
            "NPM Proxy Hosts": ("npm_hosts", _list_proxy_hosts),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "npm_host":
            host_id = fav["id"]
            return lambda hid=host_id: _host_detail(hid)


def _fetch_stats():
    hosts = _api("/nginx/proxy-hosts")
    if hosts and isinstance(hosts, list):
        enabled = sum(1 for h in hosts if h.get("enabled"))
        _HEADER_CACHE["stats"] = f"NPM: {len(hosts)} hosts ({enabled} enabled)"
    _HEADER_CACHE["timestamp"] = time.time()


def npm_menu():
    while True:
        idx = pick_option("Nginx Proxy Manager:", [
            "Add Proxy Host       — create new proxy host",
            "Auto-Generate Proxy  — scan Docker containers for services",
            "Proxy Hosts          — list and manage proxy hosts",
            "Redirection Hosts    — list redirect rules",
            "SSL Certificates     — view certificates",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 7:
            return
        elif idx == 5:
            continue
        elif idx == 6:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(NpmPlugin())
        elif idx == 0:
            _add_proxy_host()
        elif idx == 1:
            _auto_generate_proxy_hosts()
        elif idx == 2:
            _list_proxy_hosts()
        elif idx == 3:
            _list_redirects()
        elif idx == 4:
            _list_certs()


def _list_proxy_hosts():
    """List all proxy hosts."""
    while True:
        hosts = _api("/nginx/proxy-hosts")
        if not hosts or not isinstance(hosts, list):
            warn("No proxy hosts found.")
            return

        choices = []
        for h in hosts:
            domains = ", ".join(h.get("domain_names", ["?"]))
            fwd = f"{h.get('forward_scheme', 'http')}://{h.get('forward_host', '?')}:{h.get('forward_port', '?')}"
            enabled = f"{C.GREEN}●{C.RESET}" if h.get("enabled") else f"{C.DIM}○{C.RESET}"
            ssl = f" {C.GREEN}SSL{C.RESET}" if h.get("certificate_id") else ""
            choices.append(f"{enabled} {domains:<40} → {fwd}{ssl}")

        choices.append("← Back")
        idx = pick_option("Proxy Hosts:", choices)
        if idx >= len(hosts):
            return

        _host_detail(hosts[idx].get("id"))


_NPM_WRITABLE_FIELDS = {
    "domain_names", "forward_scheme", "forward_host", "forward_port",
    "certificate_id", "ssl_forced", "hsts_enabled", "hsts_subdomains",
    "http2_support", "block_exploits", "caching_enabled",
    "allow_websocket_upgrade", "access_list_id", "advanced_config",
    "enabled", "meta", "locations",
}


def _host_payload(host, **overrides):
    """Build a clean PUT payload from a host dict, stripping read-only fields."""
    payload = {k: v for k, v in host.items() if k in _NPM_WRITABLE_FIELDS}
    payload.update(overrides)
    return payload


def _host_detail(host_id):
    """Show detail and actions for a proxy host."""
    host = _api(f"/nginx/proxy-hosts/{host_id}")
    if not host:
        error("Could not fetch host details.")
        return

    domains = ", ".join(host.get("domain_names", ["?"]))
    fwd_scheme = host.get("forward_scheme", "http")
    fwd_host = host.get("forward_host", "?")
    fwd_port = host.get("forward_port", "?")
    enabled = host.get("enabled", False)
    ssl_id = host.get("certificate_id")

    print(f"\n  {C.BOLD}Proxy Host #{host_id}{C.RESET}")
    print(f"  Domains: {C.ACCENT}{domains}{C.RESET}")
    print(f"  Forward: {fwd_scheme}://{fwd_host}:{fwd_port}")
    print(f"  Enabled: {'Yes' if enabled else 'No'}")
    print(f"  SSL: {'Certificate #{}'.format(ssl_id) if ssl_id else 'None'}")
    if host.get("access_list_id"):
        print(f"  Access List: #{host['access_list_id']}")

    toggle_label = "Disable" if enabled else "Enable"
    ssl_label = "Change SSL" if ssl_id else "Assign SSL"
    choices = [toggle_label, ssl_label, "Delete", "★ Favorite", "← Back"]
    aidx = pick_option(f"Host: {domains}", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("npm_host", str(host_id), f"NPM: {domains}")
    elif al == toggle_label:
        _api(f"/nginx/proxy-hosts/{host_id}", method="PUT",
             data=_host_payload(host, enabled=not enabled))
        action = "NPM Disable Proxy Host" if enabled else "NPM Enable Proxy Host"
        log_action(action, domains)
        success(f"{'Disabled' if enabled else 'Enabled'}: {domains}")
    elif al == ssl_label:
        cert_id, ssl_forced, http2 = _pick_certificate()
        _api(f"/nginx/proxy-hosts/{host_id}", method="PUT",
             data=_host_payload(host, certificate_id=cert_id,
                                ssl_forced=ssl_forced, http2_support=http2,
                                hsts_enabled=ssl_forced))
        if cert_id:
            log_action("NPM Assign SSL", f"cert #{cert_id} → {domains}")
            success(f"SSL certificate #{cert_id} assigned to {domains}")
        else:
            log_action("NPM Remove SSL", domains)
            success(f"SSL removed from {domains}")
    elif al == "Delete":
        if confirm(f"Delete proxy host {domains}?", default_yes=False):
            _api(f"/nginx/proxy-hosts/{host_id}", method="DELETE")
            log_action("NPM Delete Proxy Host", domains)
            success(f"Deleted: {domains}")


def _list_redirects():
    """List redirect hosts."""
    redirects = _api("/nginx/redirection-hosts")
    if not redirects or not isinstance(redirects, list):
        warn("No redirection hosts found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Redirection Hosts{C.RESET} ({len(redirects)})\n")
    for r in redirects:
        domains = ", ".join(r.get("domain_names", ["?"]))
        fwd = r.get("forward_domain_name", "?")
        code = r.get("forward_http_code", "301")
        enabled = f"{C.GREEN}●{C.RESET}" if r.get("enabled") else f"{C.DIM}○{C.RESET}"
        print(f"  {enabled} {domains:<40} → {fwd} ({code})")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _list_certs():
    """List SSL certificates with actions."""
    while True:
        certs = _api("/nginx/certificates")
        if not certs or not isinstance(certs, list):
            warn("No certificates found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for c in certs:
            domains = ", ".join(c.get("domain_names", ["?"]))
            provider = c.get("provider", "?")
            expires = (c.get("expires_on") or "?")[:10]
            choices.append(f"🔒 {domains:<40} {provider:<15} expires {expires}")

        choices.append("← Back")
        idx = pick_option("SSL Certificates:", choices)
        if idx >= len(certs):
            return

        _cert_detail(certs[idx])


def _cert_detail(cert):
    """Show detail and actions for an SSL certificate."""
    cert_id = cert.get("id")
    domains = ", ".join(cert.get("domain_names", ["?"]))
    provider = cert.get("provider", "?")
    expires = cert.get("expires_on", "?")
    nice_name = cert.get("nice_name", "")

    print(f"\n  {C.BOLD}Certificate #{cert_id}{C.RESET}")
    if nice_name:
        print(f"  Name: {nice_name}")
    print(f"  Domains: {C.ACCENT}{domains}{C.RESET}")
    print(f"  Provider: {provider}")
    print(f"  Expires: {expires}")

    actions = []
    if provider == "letsencrypt":
        actions.append("Renew")
    actions.append("Delete")
    actions.append("← Back")

    aidx = pick_option(f"Certificate: {domains}", actions)
    action = actions[aidx]

    if action == "← Back":
        return
    elif action == "Renew":
        result = _api(f"/nginx/certificates/{cert_id}/renew", method="POST")
        if result is not None:
            log_action("NPM Renew Certificate", domains)
            success(f"Renewal triggered for {domains}")
        else:
            error(f"Renewal failed for {domains}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action == "Delete":
        if confirm(f"Delete certificate for {domains}?", default_yes=False):
            _api(f"/nginx/certificates/{cert_id}", method="DELETE")
            log_action("NPM Delete Certificate", domains)
            success(f"Deleted certificate: {domains}")


def _pick_certificate():
    """Let user pick an SSL certificate. Returns (cert_id, ssl_forced, http2) or (0, False, False)."""
    certs = _api("/nginx/certificates")
    if not certs or not isinstance(certs, list):
        warn("No SSL certificates available.")
        return 0, False, False

    choices = []
    for c in certs:
        domains = ", ".join(c.get("domain_names", ["?"]))
        provider = c.get("provider", "?")
        expires = c.get("expires_on", "?")[:10] if c.get("expires_on") else "?"
        choices.append(f"{domains}  ({provider}, expires {expires})")
    choices.append("None (no SSL)")
    choices.append("Cancel")

    idx = pick_option("Assign SSL certificate:", choices)
    if idx >= len(certs):
        return 0, False, False

    cert_id = certs[idx].get("id", 0)
    ssl_forced = confirm("Force SSL (redirect HTTP → HTTPS)?")
    http2 = confirm("Enable HTTP/2?")
    return cert_id, ssl_forced, http2


def _add_proxy_host():
    """Create a new proxy host."""
    domain = prompt_text("Domain name (e.g. app.example.com):")
    if not domain:
        return

    fwd_host = prompt_text("Forward hostname/IP (e.g. 192.168.1.50):")
    if not fwd_host:
        return

    fwd_port = prompt_text("Forward port (e.g. 8080):")
    if not fwd_port:
        return

    try:
        fwd_port = int(fwd_port)
    except ValueError:
        error("Port must be a number.")
        return

    # SSL certificate selection
    cert_id, ssl_forced, http2 = _pick_certificate()

    data = {
        "domain_names": [domain],
        "forward_scheme": "https" if ssl_forced else "http",
        "forward_host": fwd_host,
        "forward_port": fwd_port,
        "access_list_id": "0",
        "certificate_id": cert_id,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False},
        "advanced_config": "",
        "locations": [],
        "block_exploits": True,
        "caching_enabled": False,
        "allow_websocket_upgrade": True,
        "http2_support": http2,
        "hsts_enabled": ssl_forced,
        "hsts_subdomains": False,
        "ssl_forced": ssl_forced,
        "enabled": True,
    }

    result = _api("/nginx/proxy-hosts", method="POST", data=data)
    if result:
        ssl_note = f" with SSL cert #{cert_id}" if cert_id else ""
        success(f"Created proxy host: {domain} → {fwd_host}:{fwd_port}{ssl_note}")
        log_action("NPM Add Proxy Host", f"{domain} → {fwd_host}:{fwd_port}{ssl_note}")
    else:
        error("Failed to create proxy host.")


# ─── Auto-Generate Proxy Hosts ────────────────────────────────────────────

def _gather_ssh_hosts():
    """Collect all available SSH hosts for Docker container scanning."""
    hosts = []
    unraid = CFG.get("unraid_ssh_host", "")
    if unraid:
        hosts.append({"name": "Unraid", "host": unraid})
    for s in CFG.get("docker_servers", []):
        hosts.append({
            "name": s.get("name", "?"), "host": s.get("host", ""),
            "port": s.get("port", ""),
        })
    return hosts


def _parse_docker_ports(ports_str):
    """Parse Docker port string like '0.0.0.0:8080->80/tcp, :::8080->80/tcp'.

    Returns list of (host_port, container_port) tuples for IPv4 bindings.
    """
    results = []
    if not ports_str or ports_str.strip() == "":
        return results
    for mapping in ports_str.split(","):
        mapping = mapping.strip()
        # Match patterns like 0.0.0.0:8080->80/tcp or 8080->80/tcp
        m = re.match(r'(?:[\d.]+:)?(\d+)->(\d+)/\w+', mapping)
        if m:
            host_port = int(m.group(1))
            container_port = int(m.group(2))
            results.append((host_port, container_port))
    return results


def _extract_server_ip(host_str):
    """Extract IP or hostname from SSH host string like 'root@10.0.0.5'."""
    if "@" in host_str:
        return host_str.split("@", 1)[1]
    return host_str


def _auto_generate_proxy_hosts():
    """Scan Docker containers on a server and auto-create proxy hosts."""
    hosts = _gather_ssh_hosts()
    if not hosts:
        warn("No SSH hosts configured. Add an Unraid host or Docker server first.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Pick server to scan
    choices = [f"{h['name']:<20} {C.DIM}{h['host']}{C.RESET}" for h in hosts]
    choices.append("← Back")
    idx = pick_option("Scan which server?", choices)
    if idx >= len(hosts):
        return

    selected = hosts[idx]
    ssh_host = selected["host"]
    port = selected.get("port", "")

    # SSH to get container names + ports
    info(f"Scanning containers on {selected['name']}...")
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if port:
        ssh_cmd.extend(["-p", port])
    ssh_cmd.extend([
        ssh_host,
        "docker ps --format '{{.Names}}\\t{{.Ports}}' 2>/dev/null"
    ])
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True,
                                timeout=15, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        error("SSH connection timed out.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    if result.returncode != 0 or not result.stdout.strip():
        error("Failed to list containers (is Docker installed?).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Parse containers with exposed ports
    server_ip = _extract_server_ip(ssh_host)
    candidates = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        cname = parts[0].strip()
        ports_str = parts[1].strip() if len(parts) > 1 else ""
        port_mappings = _parse_docker_ports(ports_str)
        if port_mappings:
            # Use the first port mapping
            host_port, container_port = port_mappings[0]
            candidates.append({
                "name": cname,
                "host_ip": server_ip,
                "host_port": host_port,
                "all_ports": port_mappings,
            })

    if not candidates:
        warn("No containers with exposed ports found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Get existing proxy hosts for deduplication
    existing = _api("/nginx/proxy-hosts")
    existing_forwards = set()
    existing_domains = {}
    if existing and isinstance(existing, list):
        for h in existing:
            fwd_host = h.get("forward_host", "")
            fwd_port = h.get("forward_port", 0)
            existing_forwards.add(f"{fwd_host}:{fwd_port}")
            domains = ", ".join(h.get("domain_names", []))
            existing_domains[f"{fwd_host}:{fwd_port}"] = domains

    # Build selection list
    display = []
    for c in candidates:
        key = f"{c['host_ip']}:{c['host_port']}"
        if key in existing_forwards:
            domain = existing_domains.get(key, "?")
            display.append(f"{c['name']:<20} :{c['host_port']}  {C.DIM}(already proxied: {domain}){C.RESET}")
        else:
            display.append(f"{c['name']:<20} :{c['host_port']}  {C.GREEN}(new){C.RESET}")

    selected_indices = pick_multi("Select services to create proxy hosts for:", display)
    if not selected_indices:
        return

    selected_candidates = [candidates[i] for i in selected_indices]

    # Ask for SSL certificate once for the batch
    cert_id, ssl_forced, http2 = 0, False, False
    if confirm("Apply SSL certificate to all new hosts?", default_yes=False):
        cert_id, ssl_forced, http2 = _pick_certificate()

    # Create proxy hosts
    created = 0
    for c in selected_candidates:
        default_domain = f"{c['name']}.local"
        domain = prompt_text(f"Domain for {c['name']} (port {c['host_port']}):",
                             default=default_domain)
        if not domain:
            continue

        data = {
            "domain_names": [domain],
            "forward_scheme": "http",
            "forward_host": c["host_ip"],
            "forward_port": c["host_port"],
            "access_list_id": "0",
            "certificate_id": cert_id,
            "meta": {"letsencrypt_agree": False, "dns_challenge": False},
            "advanced_config": "",
            "locations": [],
            "block_exploits": True,
            "caching_enabled": False,
            "allow_websocket_upgrade": True,
            "http2_support": http2,
            "hsts_enabled": ssl_forced,
            "hsts_subdomains": False,
            "ssl_forced": ssl_forced,
            "enabled": True,
        }

        result = _api("/nginx/proxy-hosts", method="POST", data=data)
        if result:
            log_action("NPM Auto-Generate Proxy Host", f"{domain} → {c['host_ip']}:{c['host_port']}")
            created += 1
        else:
            error(f"Failed to create proxy host for {c['name']}")

    if created:
        success(f"Created {created} proxy host(s)")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
