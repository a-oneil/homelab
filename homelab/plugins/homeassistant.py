"""Home Assistant plugin — browse entities and call services."""

import json
import subprocess
import time
import urllib.request

from homelab.config import CFG, save_config
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, prompt_text, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to Home Assistant."""
    base = CFG.get("homeassistant_url", "").rstrip("/")
    token = CFG.get("homeassistant_token", "")
    if not base or not token:
        return None

    url = f"{base}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if data:
        payload = json.dumps(data).encode()
    else:
        payload = None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Home Assistant API error: {e}")
        return None


class HomeAssistantPlugin(Plugin):
    name = "Home Assistant"
    key = "homeassistant"

    def is_configured(self):
        return bool(CFG.get("homeassistant_url") and CFG.get("homeassistant_token"))

    def get_config_fields(self):
        return [
            ("homeassistant_url", "HA URL", "e.g. http://192.168.1.100:8123", False),
            ("homeassistant_token", "HA Token", "Long-lived access token", True),
            ("homeassistant_ssh_host", "HA SSH Host", "e.g. root@192.168.1.100", False),
            ("homeassistant_ssh_port", "HA SSH Port", "e.g. 22222 (leave blank for 22)", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("Home Assistant       — entities and automations", ha_menu),
        ]

    def get_actions(self):
        return {
            "HA Browse Entities": ("ha_browse", _browse_entities),
            "HA Call Service": ("ha_service", _call_service),
            "HA Dashboard": ("ha_dashboard", _dashboard_view),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "ha_entity":
            entity_id = fav["id"]
            return lambda eid=entity_id: _show_entity_by_id(eid)
        elif fav.get("type") == "ha_service_call":
            entity_id = fav["id"]
            service = fav.get("service", "toggle")
            return lambda eid=entity_id, svc=service: _resolve_service_call(eid, svc)


def _fetch_stats():
    states = _api("/api/states")
    if states and isinstance(states, list):
        _HEADER_CACHE["stats"] = f"HA: {len(states)} entities"
    _HEADER_CACHE["timestamp"] = time.time()


def ha_menu():
    ssh_host = CFG.get("homeassistant_ssh_host", "")
    while True:
        items = [
            "Browse Entities       — search and view entity states",
            "Call Service          — perform action on entity",
            "Dashboard View        — quick glance at key entities",
        ]
        if ssh_host:
            items.append("SSH Shell             — open a terminal session")
        items.extend([
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        idx = pick_option("Home Assistant:", items)
        label = items[idx]
        if label == "← Back":
            return
        elif label.startswith("───"):
            continue
        elif label.startswith("★"):
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(HomeAssistantPlugin())
        elif idx == 0:
            _browse_entities()
        elif idx == 1:
            _call_service()
        elif idx == 2:
            _dashboard_view()
        elif label.startswith("SSH Shell"):
            _ha_ssh_shell()


def _ha_ssh_shell():
    """Open an interactive SSH session to the Home Assistant host."""
    host = CFG.get("homeassistant_ssh_host", "")
    if not host:
        error("SSH host not configured. Set 'HA SSH Host' in plugin settings.")
        return
    port = str(CFG.get("homeassistant_ssh_port", "")).strip()
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", port])
    cmd.append(host)
    info(f"Connecting to {host}" + (f" port {port}" if port else "") + "...")
    subprocess.run(cmd)


def _fetch_entities():
    """Fetch all entities and group by domain."""
    states = _api("/api/states")
    if not states or not isinstance(states, list):
        error("Could not fetch entities.")
        return None
    domains = {}
    for entity in states:
        entity_id = entity.get("entity_id", "")
        domain = entity_id.split(".")[0] if "." in entity_id else "other"
        domains.setdefault(domain, []).append(entity)
    return domains


def _pick_entity(domains, prompt_msg="Select domain:"):
    """Let user pick domain then entity. Returns entity dict or None."""
    sorted_domains = sorted(domains.keys())
    choices = [f"{d} ({len(domains[d])} entities)" for d in sorted_domains]
    choices.append("← Back")

    idx = pick_option(prompt_msg, choices)
    if idx >= len(sorted_domains):
        return None

    domain = sorted_domains[idx]
    entities = sorted(domains[domain], key=lambda x: x.get("entity_id", ""))

    entity_choices = []
    for e in entities:
        eid = e.get("entity_id", "?")
        state = e.get("state", "?")
        friendly = e.get("attributes", {}).get("friendly_name", "")
        label = f"{friendly or eid}  [{state}]"
        entity_choices.append(label)
    entity_choices.append("← Back")

    eidx = pick_option(f"{domain} entities:", entity_choices)
    if eidx >= len(entities):
        return None

    return entities[eidx]


def _browse_entities():
    domains = _fetch_entities()
    if not domains:
        return

    while True:
        entity = _pick_entity(domains)
        if not entity:
            return
        _show_entity_detail(entity)


def _resolve_service_call(entity_id, service):
    """Execute a favorited service call (fetches current name)."""
    domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
    # Try to get friendly name
    states = _api("/api/states")
    friendly = entity_id
    if states:
        for e in states:
            if e.get("entity_id") == entity_id:
                friendly = e.get("attributes", {}).get("friendly_name", entity_id)
                break
    _run_service_call(entity_id, domain, service, friendly)


def _show_entity_by_id(entity_id):
    """Fetch and show a single entity by ID (used by favorites)."""
    states = _api("/api/states")
    if not states:
        error("Could not fetch entities.")
        return
    for e in states:
        if e.get("entity_id") == entity_id:
            _show_entity_detail(e)
            return
    error(f"Entity {entity_id} not found.")


def _domain_actions(domain):
    """Return list of (label, service) tuples for an entity domain."""
    mapping = {
        "light": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "switch": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "fan": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "input_boolean": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "button": [("Press", "press")],
        "input_button": [("Press", "press")],
        "automation": [("Trigger", "trigger"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "script": [("Run", "turn_on")],
        "scene": [("Activate", "turn_on")],
        "lock": [("Lock", "lock"), ("Unlock", "unlock")],
        "cover": [("Open", "open_cover"), ("Close", "close_cover"), ("Stop", "stop_cover")],
        "media_player": [("Play/Pause", "media_play_pause"), ("Stop", "media_stop"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "climate": [("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "vacuum": [("Start", "start"), ("Stop", "stop"), ("Return home", "return_to_base")],
        "siren": [("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "alarm_control_panel": [("Arm away", "alarm_arm_away"), ("Arm home", "alarm_arm_home"), ("Disarm", "alarm_disarm")],
        "number": [],
        "select": [],
        "sensor": [],
        "binary_sensor": [],
    }
    return mapping.get(domain, [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")])


def _show_entity_detail(entity):
    eid = entity.get("entity_id", "?")
    attrs = entity.get("attributes", {})
    friendly = attrs.get("friendly_name", eid)
    domain = eid.split(".")[0]

    while True:
        # Re-fetch current state
        fresh = _api(f"/api/states/{eid}")
        if fresh:
            state = fresh.get("state", "?")
            attrs = fresh.get("attributes", {})
        else:
            state = entity.get("state", "?")

        print(f"\n  {C.BOLD}{friendly}{C.RESET}")
        print(f"  {C.DIM}Entity ID: {eid}{C.RESET}")
        print(f"  {C.BOLD}State:{C.RESET} {C.ACCENT}{state}{C.RESET}")

        if attrs:
            print(f"\n  {C.BOLD}Attributes:{C.RESET}")
            for k, v in attrs.items():
                if k != "friendly_name":
                    print(f"    {k}: {v}")

        domain_acts = _domain_actions(domain)
        action_choices = [label for label, _ in domain_acts]
        action_choices.extend(["★ Favorite", "← Back"])

        print()
        aidx = pick_option(f"Action for {friendly}:", action_choices)
        al = action_choices[aidx]

        if al == "← Back":
            return
        elif al == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite("ha_entity", eid, friendly)
        else:
            # Find the matching service
            for label, svc in domain_acts:
                if label == al:
                    _api(f"/api/services/{domain}/{svc}", method="POST", data={"entity_id": eid})
                    success(f"{al}: {friendly}")
                    break


def _call_service():
    domains = _fetch_entities()
    if not domains:
        return

    entity = _pick_entity(domains, prompt_msg="Call service — select domain:")
    if not entity:
        return

    entity_id = entity.get("entity_id", "")
    friendly = entity.get("attributes", {}).get("friendly_name", entity_id)
    domain = entity_id.split(".")[0]

    domain_acts = _domain_actions(domain)
    service_choices = [f"{label} ({svc})" for label, svc in domain_acts]
    service_choices.append("Custom service")
    service_choices.append("Cancel")

    idx = pick_option(f"Service for {friendly}:", service_choices)
    if service_choices[idx] == "Cancel":
        return

    if service_choices[idx] == "Custom service":
        service = prompt_text("Service name (e.g. turn_on):")
        if not service:
            return
        label = service
    else:
        label, service = domain_acts[idx]

    while True:
        action_choices = ["Run now", "Run again", "★ Favorite", "← Back"]
        aidx = pick_option(f"{label} — {friendly}:", action_choices)
        al = action_choices[aidx]

        if al == "← Back":
            return
        elif al == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite(
                "ha_service_call", entity_id,
                f"HA: {label} {friendly}",
                service=service,
            )
        elif al in ("Run now", "Run again"):
            _run_service_call(entity_id, domain, service, friendly)


def _run_service_call(entity_id, domain, service, friendly):
    """Execute a service call on an entity."""
    result = _api(
        f"/api/services/{domain}/{service}",
        method="POST",
        data={"entity_id": entity_id},
    )
    if result is not None:
        success(f"Called {domain}.{service} on {friendly}")
    else:
        error("Service call failed.")


# ─── Dashboard View ────────────────────────────────────────────────────────

def _dashboard_view():
    """Show a configurable at-a-glance view of key entities."""
    dashboard_ids = CFG.get("ha_dashboard_entities", [])

    while True:
        if not dashboard_ids:
            print(f"\n  {C.DIM}No dashboard entities configured.{C.RESET}")
            idx = pick_option("Dashboard:", ["+ Add entities", "← Back"])
            if idx == 1:
                return
            _configure_dashboard()
            dashboard_ids = CFG.get("ha_dashboard_entities", [])
            continue

        # Fetch all states once
        states = _api("/api/states")
        if not states:
            error("Could not fetch entity states.")
            return

        state_map = {e.get("entity_id"): e for e in states}

        print(f"\n  {C.BOLD}Home Assistant Dashboard{C.RESET}\n")
        for eid in dashboard_ids:
            entity = state_map.get(eid)
            if not entity:
                print(f"  {C.DIM}○{C.RESET} {eid}  {C.RED}not found{C.RESET}")
                continue

            state = entity.get("state", "?")
            attrs = entity.get("attributes", {})
            friendly = attrs.get("friendly_name", eid)
            domain = eid.split(".")[0]
            unit = attrs.get("unit_of_measurement", "")

            # Color-code based on state
            if state in ("on", "home", "open", "unlocked"):
                icon = f"{C.GREEN}●{C.RESET}"
                state_str = f"{C.GREEN}{state}{C.RESET}"
            elif state in ("off", "away", "closed", "locked"):
                icon = f"{C.DIM}○{C.RESET}"
                state_str = f"{C.DIM}{state}{C.RESET}"
            elif state == "unavailable":
                icon = f"{C.RED}●{C.RESET}"
                state_str = f"{C.RED}{state}{C.RESET}"
            else:
                icon = f"{C.ACCENT}●{C.RESET}"
                state_str = f"{C.ACCENT}{state}{C.RESET}"

            if unit:
                state_str += f" {unit}"

            # Extra info for specific domains
            extra = ""
            if domain == "climate":
                temp = attrs.get("current_temperature", "")
                target = attrs.get("temperature", "")
                if temp:
                    extra = f"  (current: {temp}, target: {target})"
            elif domain == "media_player" and state == "playing":
                title = attrs.get("media_title", "")
                if title:
                    extra = f"  ({title})"

            print(f"  {icon} {friendly:<35} {state_str}{extra}")

        print()
        idx = pick_option("Dashboard:", [
            "Refresh", "Edit entity list", "← Back",
        ])
        if idx == 2:
            return
        elif idx == 0:
            continue
        elif idx == 1:
            _configure_dashboard()
            dashboard_ids = CFG.get("ha_dashboard_entities", [])


def _configure_dashboard():
    """Let user pick entities for the dashboard view."""
    while True:
        current = CFG.get("ha_dashboard_entities", [])
        print(f"\n  {C.BOLD}Dashboard Entities{C.RESET} ({len(current)} configured)\n")
        for eid in current:
            print(f"    {eid}")

        idx = pick_option("Configure:", [
            "+ Add entities", "Remove entity", "Clear all", "← Done",
        ])

        if idx == 3:
            return
        elif idx == 2:
            CFG["ha_dashboard_entities"] = []
            save_config(CFG)
            success("Dashboard cleared.")
        elif idx == 1:
            if not current:
                warn("No entities to remove.")
                continue
            choices = current + ["Cancel"]
            ridx = pick_option("Remove which?", choices)
            if ridx < len(current):
                removed = current.pop(ridx)
                CFG["ha_dashboard_entities"] = current
                save_config(CFG)
                success(f"Removed: {removed}")
        elif idx == 0:
            # Let user pick from available entities
            domains = _fetch_entities()
            if not domains:
                continue
            entity = _pick_entity(domains, prompt_msg="Add entity to dashboard:")
            if entity:
                eid = entity.get("entity_id", "")
                if eid and eid not in current:
                    current.append(eid)
                    CFG["ha_dashboard_entities"] = current
                    save_config(CFG)
                    friendly = entity.get("attributes", {}).get("friendly_name", eid)
                    success(f"Added: {friendly}")
                elif eid in current:
                    warn(f"{eid} is already on the dashboard.")
