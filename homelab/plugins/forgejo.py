"""Forgejo plugin — repos, CI runners, issues via Gitea-compatible API."""

import json
import os
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, prompt_text, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to Forgejo."""
    base = CFG.get("forgejo_url", "").rstrip("/")
    token = CFG.get("forgejo_token", "")
    if not base or not token:
        return None

    url = f"{base}/api/v1{endpoint}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Forgejo API error: {e}")
        return None


class ForgejoPlugin(Plugin):
    name = "Forgejo"
    key = "forgejo"

    def is_configured(self):
        return bool(CFG.get("forgejo_url") and CFG.get("forgejo_token"))

    def get_config_fields(self):
        return [
            ("forgejo_url", "Forgejo URL", "e.g. http://192.168.1.100:3000", False),
            ("forgejo_token", "Forgejo Token", "personal access token", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        repos = _api("/repos/search?limit=50&sort=updated")
        lines = []
        if repos and repos.get("data"):
            repo_list = repos["data"]
            lines.append(f"{len(repo_list)} repositories")
            for r in repo_list[:3]:
                name = r.get("full_name", "?")
                updated = r.get("updated_at", "?")[:10]
                private = f" {C.YELLOW}●{C.RESET}" if r.get("private") else ""
                lines.append(f"  {name}{private}  {C.DIM}updated {updated}{C.RESET}")
        if not lines:
            return []
        return [{"title": "Forgejo", "lines": lines}]

    def get_menu_items(self):
        return [
            ("Forgejo              — repos, CI runners, issues", forgejo_menu),
        ]

    def get_actions(self):
        return {
            "Forgejo Repos": ("forgejo_repos", _list_repos),
            "Forgejo Runners": ("forgejo_runners", _list_runners),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "forgejo_repo":
            full_name = fav["id"]
            return lambda fn=full_name: _repo_detail(fn)


def _fetch_stats():
    repos = _api("/repos/search?limit=1")
    if repos and isinstance(repos.get("data"), list):
        all_repos = _api("/repos/search?limit=50")
        if all_repos:
            count = len(all_repos.get("data", []))
            _HEADER_CACHE["stats"] = f"Forgejo: {count} repos"
    _HEADER_CACHE["timestamp"] = time.time()


def forgejo_menu():
    while True:
        idx = pick_option("Forgejo:", [
            "Repositories         — browse and manage repos",
            "Create Repository    — create a new repo",
            "CI Runners           — view runner status",
            "Issues               — open issues across repos",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 6:
            return
        elif idx == 4:
            continue
        elif idx == 5:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(ForgejoPlugin())
        elif idx == 0:
            _list_repos()
        elif idx == 1:
            _create_repo()
        elif idx == 2:
            _list_runners()
        elif idx == 3:
            _list_issues()


def _list_repos():
    """List all repositories."""
    while True:
        repos = _api("/repos/search?limit=50&sort=updated")
        if not repos or not repos.get("data"):
            warn("No repositories found.")
            return

        choices = []
        repo_list = repos["data"]
        for r in repo_list:
            name = r.get("full_name", "?")
            stars = r.get("stars_count", 0)
            forks = r.get("forks_count", 0)
            private = f" {C.YELLOW}●{C.RESET}" if r.get("private") else ""
            desc = r.get("description", "")[:40]
            choices.append(f"{name:<35}{private}  ★{stars} ⑂{forks}  {C.DIM}{desc}{C.RESET}")

        choices.append("← Back")
        idx = pick_option("Repositories:", choices)
        if idx >= len(repo_list):
            return

        _repo_detail(repo_list[idx].get("full_name", ""))


def _repo_detail(full_name):
    """Show detail for a single repository."""
    repo = _api(f"/repos/{full_name}")
    if not repo:
        error(f"Repository {full_name} not found.")
        return

    print(f"\n  {C.BOLD}{full_name}{C.RESET}")
    if repo.get("description"):
        print(f"  {C.DIM}{repo['description']}{C.RESET}")
    print(f"  Stars: {repo.get('stars_count', 0)}  Forks: {repo.get('forks_count', 0)}")
    print(f"  Default branch: {repo.get('default_branch', '?')}")
    print(f"  Size: {repo.get('size', 0)} KB")
    print(f"  Private: {'Yes' if repo.get('private') else 'No'}")
    if repo.get("updated_at"):
        print(f"  Updated: {repo['updated_at'][:10]}")

    choices = ["Git Clone", "View Issues", "View Branches", "★ Favorite", "← Back"]
    aidx = pick_option(f"Repo: {full_name}", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("forgejo_repo", full_name, f"Forgejo: {full_name}")
    elif al == "Git Clone":
        _clone_repo(repo)
    elif al == "View Issues":
        _repo_issues(full_name)
    elif al == "View Branches":
        _repo_branches(full_name)


def _repo_issues(full_name):
    """List issues for a repo."""
    issues = _api(f"/repos/{full_name}/issues?state=open&limit=20")
    if not issues or not isinstance(issues, list):
        warn("No open issues.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Open Issues — {full_name}{C.RESET}\n")
    for issue in issues:
        num = issue.get("number", "?")
        title = issue.get("title", "?")[:60]
        labels = " ".join(f"[{label.get('name', '')}]" for label in issue.get("labels", []))
        print(f"  #{num:<5} {title}  {C.DIM}{labels}{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _repo_branches(full_name):
    """List branches for a repo."""
    branches = _api(f"/repos/{full_name}/branches")
    if not branches or not isinstance(branches, list):
        warn("No branches found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Branches — {full_name}{C.RESET}\n")
    for b in branches:
        name = b.get("name", "?")
        protected = f" {C.YELLOW}protected{C.RESET}" if b.get("protected") else ""
        commit = b.get("commit", {}).get("id", "?")[:8]
        print(f"  {name:<30} {C.DIM}{commit}{C.RESET}{protected}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _list_runners():
    """List CI runners (Forgejo Actions)."""
    # Try admin endpoint first, fall back to user endpoint
    runners = _api("/admin/runners")
    if not runners:
        runners = _api("/user/actions/runners")
    if not runners or not isinstance(runners, list):
        warn("No runners found or insufficient permissions.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}CI Runners{C.RESET} ({len(runners)})\n")
    for r in runners:
        name = r.get("name", "?")
        online = r.get("online", False)
        busy = r.get("busy", False)
        labels = ", ".join(label.get("name", "") for label in r.get("labels", []))

        if busy:
            icon = f"{C.YELLOW}●{C.RESET}"
            status = "busy"
        elif online:
            icon = f"{C.GREEN}●{C.RESET}"
            status = "idle"
        else:
            icon = f"{C.RED}●{C.RESET}"
            status = "offline"

        print(f"  {icon} {name:<25} [{status}]  {C.DIM}{labels}{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _list_issues():
    """List open issues across all repos."""
    # Get user's repos then aggregate issues
    repos = _api("/repos/search?limit=50")
    if not repos or not repos.get("data"):
        warn("No repositories found.")
        return

    all_issues = []
    for repo in repos["data"]:
        full_name = repo.get("full_name", "")
        if repo.get("has_issues"):
            issues = _api(f"/repos/{full_name}/issues?state=open&limit=10")
            if issues and isinstance(issues, list):
                for issue in issues:
                    issue["_repo"] = full_name
                    all_issues.append(issue)

    if not all_issues:
        info("No open issues across any repository.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for issue in all_issues:
        repo = issue.get("_repo", "?")
        num = issue.get("number", "?")
        title = issue.get("title", "?")[:50]
        rows.append(f"{repo} #{num}  {title}")

    scrollable_list(f"Open Issues ({len(rows)}):", rows)


def _create_repo():
    """Create a new Forgejo repository."""
    name = prompt_text("Repository name:")
    if not name:
        return

    desc = prompt_text("Description (optional):", default="")
    private = confirm("Private repository?", default_yes=True)
    init = confirm("Initialize with README?", default_yes=True)

    data = {
        "name": name,
        "description": desc,
        "private": private,
        "auto_init": init,
        "default_branch": "main",
    }

    if init:
        gitignore_idx = pick_option("Add .gitignore template?", [
            "None", "Python", "Go", "Node", "Rust", "Java", "Cancel",
        ])
        gitignore_map = {1: "Python", 2: "Go", 3: "Node", 4: "Rust", 5: "Java"}
        if gitignore_idx == 6:
            return
        if gitignore_idx in gitignore_map:
            data["gitignores"] = gitignore_map[gitignore_idx]

        license_idx = pick_option("Add license?", [
            "None", "MIT", "Apache-2.0", "GPL-3.0", "AGPL-3.0", "Cancel",
        ])
        license_map = {1: "MIT", 2: "Apache-2.0", 3: "GPL-3.0", 4: "AGPL-3.0"}
        if license_idx == 5:
            return
        if license_idx in license_map:
            data["license"] = license_map[license_idx]

    result = _api("/user/repos", method="POST", data=data)
    if result:
        full_name = result.get("full_name", name)
        clone_url = result.get("clone_url", "")
        success(f"Created repository: {full_name}")
        if clone_url and confirm("Clone to local machine?"):
            _clone_repo(result)
    else:
        error("Failed to create repository.")


def _clone_repo(repo):
    """Clone a Forgejo repository to localhost."""
    clone_url = repo.get("clone_url", "")
    ssh_url = repo.get("ssh_url", "")
    full_name = repo.get("full_name", "?")

    if not clone_url and not ssh_url:
        error("No clone URL available.")
        return

    # Choose protocol
    if ssh_url and clone_url:
        proto_idx = pick_option(f"Clone {full_name}:", [
            f"SSH     — {ssh_url}",
            f"HTTPS   — {clone_url}",
            "Cancel",
        ])
        if proto_idx == 2:
            return
        url = ssh_url if proto_idx == 0 else clone_url
    else:
        url = clone_url or ssh_url

    # Choose destination
    default_dir = os.path.expanduser("~/Projects")
    dest_dir = prompt_text("Clone into directory:", default=default_dir)
    if not dest_dir:
        return
    dest_dir = os.path.expanduser(dest_dir)

    repo_name = repo.get("name", full_name.split("/")[-1])
    target = os.path.join(dest_dir, repo_name)

    if os.path.exists(target):
        error(f"Directory already exists: {target}")
        return

    os.makedirs(dest_dir, exist_ok=True)

    print(f"\n  Cloning {full_name} → {target}...")
    try:
        result = subprocess.run(
            ["git", "clone", url, target],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            success(f"Cloned to {target}")
        else:
            error(f"Git clone failed: {result.stderr.strip()}")
    except FileNotFoundError:
        error("git not found. Install git to use this feature.")
    except subprocess.TimeoutExpired:
        error("Clone timed out.")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
