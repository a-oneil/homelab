"""GitHub plugin — repos, Actions runners, issues, PRs via GitHub REST API."""

import json
import os
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, prompt_text, info, success, error, warn
from homelab.auditlog import log_action

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to GitHub."""
    token = CFG.get("github_token", "")
    if not token:
        return None

    url = f"https://api.github.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            if not body:
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return {}
        error(f"GitHub API error: {e.code} {e.reason}")
        return None
    except Exception as e:
        error(f"GitHub API error: {e}")
        return None


def _api_paginated(endpoint, limit=50):
    """Fetch paginated results from GitHub API."""
    results = []
    page = 1
    per_page = min(limit, 100)
    while len(results) < limit:
        sep = "&" if "?" in endpoint else "?"
        data = _api(f"{endpoint}{sep}per_page={per_page}&page={page}")
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        results.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return results[:limit]


class GitHubPlugin(Plugin):
    name = "GitHub"
    key = "github"

    def is_configured(self):
        return bool(CFG.get("github_token"))

    def get_config_fields(self):
        return [
            ("github_token", "GitHub Token", "personal access token (classic or fine-grained)", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        repos = _api_paginated("/user/repos?sort=updated&direction=desc", limit=50)
        lines = []
        if repos:
            lines.append(f"{len(repos)} repositories")
            for r in repos[:3]:
                name = r.get("full_name", "?")
                updated = r.get("updated_at", "?")[:10]
                private = f" {C.YELLOW}●{C.RESET}" if r.get("private") else ""
                lines.append(f"  {name}{private}  {C.DIM}updated {updated}{C.RESET}")
        if not lines:
            return []
        return [{"title": "GitHub", "lines": lines}]

    def get_menu_items(self):
        return [
            ("GitHub               — repos, Actions, issues, PRs", github_menu),
        ]

    def get_actions(self):
        return {
            "GitHub Repos": ("github_repos", _list_repos),
            "GitHub Runners": ("github_runners", _list_runners),
            "GitHub Pull Requests": ("github_prs", _list_pull_requests),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "github_repo":
            full_name = fav["id"]
            return lambda fn=full_name: _repo_detail(fn)


def _fetch_stats():
    repos = _api_paginated("/user/repos?sort=updated&direction=desc", limit=50)
    if repos:
        count = len(repos)
        _HEADER_CACHE["stats"] = f"GitHub: {count} repos"
    _HEADER_CACHE["timestamp"] = time.time()


def github_menu():
    while True:
        idx = pick_option("GitHub:", [
            "Actions Runners      — view runner status",
            "Create Repository    — create a new repo",
            "Issues               — open issues across repos",
            "Pull Requests        — open PRs across repos",
            "Repositories         — browse and manage repos",
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
            add_plugin_favorite(GitHubPlugin())
        elif idx == 0:
            _list_runners()
        elif idx == 1:
            _create_repo()
        elif idx == 2:
            _list_issues()
        elif idx == 3:
            _list_pull_requests()
        elif idx == 4:
            _list_repos()


def _list_repos():
    """List all repositories."""
    while True:
        repos = _api_paginated("/user/repos?sort=updated&direction=desc", limit=50)
        if not repos:
            warn("No repositories found.")
            return

        choices = []
        for r in repos:
            name = r.get("full_name", "?")
            stars = r.get("stargazers_count", 0)
            forks = r.get("forks_count", 0)
            private = f" {C.YELLOW}●{C.RESET}" if r.get("private") else ""
            desc = (r.get("description") or "")[:40]
            choices.append(f"{name:<35}{private}  ★{stars} ⑂{forks}  {C.DIM}{desc}{C.RESET}")

        choices.append("← Back")
        idx = pick_option("Repositories:", choices)
        if idx >= len(repos):
            return

        _repo_detail(repos[idx].get("full_name", ""))


def _repo_detail(full_name):
    """Show detail for a single repository."""
    repo = _api(f"/repos/{full_name}")
    if not repo:
        error(f"Repository {full_name} not found.")
        return

    print(f"\n  {C.BOLD}{full_name}{C.RESET}")
    if repo.get("description"):
        print(f"  {C.DIM}{repo['description']}{C.RESET}")
    print(f"  Stars: {repo.get('stargazers_count', 0)}  Forks: {repo.get('forks_count', 0)}")
    print(f"  Default branch: {repo.get('default_branch', '?')}")
    lang = repo.get("language")
    if lang:
        print(f"  Language: {lang}")
    print(f"  Size: {repo.get('size', 0)} KB")
    print(f"  Private: {'Yes' if repo.get('private') else 'No'}")
    if repo.get("updated_at"):
        print(f"  Updated: {repo['updated_at'][:10]}")

    choices = [
        "Git Clone", "View Issues", "View Branches",
        "Actions Runs", "Webhooks", "Settings",
        "★ Favorite", "← Back",
    ]
    aidx = pick_option(f"Repo: {full_name}", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("github_repo", full_name, f"GitHub: {full_name}")
    elif al == "Git Clone":
        _clone_repo(repo)
    elif al == "View Issues":
        _repo_issues(full_name)
    elif al == "View Branches":
        _repo_branches(full_name)
    elif al == "Actions Runs":
        _repo_ci_runs(full_name)
    elif al == "Webhooks":
        _repo_webhooks(full_name)
    elif al == "Settings":
        _repo_settings(full_name, repo)


def _repo_issues(full_name):
    """List issues for a repo."""
    issues = _api_paginated(f"/repos/{full_name}/issues?state=open", limit=20)
    if not issues:
        warn("No open issues.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # GitHub returns PRs in the issues endpoint — filter them out
    issues = [i for i in issues if "pull_request" not in i]
    if not issues:
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
    branches = _api_paginated(f"/repos/{full_name}/branches", limit=30)
    if not branches:
        warn("No branches found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Branches — {full_name}{C.RESET}\n")
    for b in branches:
        name = b.get("name", "?")
        protected = f" {C.YELLOW}protected{C.RESET}" if b.get("protected") else ""
        commit = b.get("commit", {}).get("sha", "?")[:8]
        print(f"  {name:<30} {C.DIM}{commit}{C.RESET}{protected}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _list_runners():
    """List GitHub Actions self-hosted runners."""
    # Try user-level runners
    runners_data = _api("/user/actions/runners")
    runners = []
    if runners_data and isinstance(runners_data, dict):
        runners = runners_data.get("runners", [])

    if not runners:
        warn("No self-hosted runners found (check token permissions).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Actions Runners{C.RESET} ({len(runners)})\n")
    for r in runners:
        name = r.get("name", "?")
        status = r.get("status", "offline")
        busy = r.get("busy", False)
        labels = ", ".join(label.get("name", "") for label in r.get("labels", []))

        if busy:
            icon = f"{C.YELLOW}●{C.RESET}"
            display = "busy"
        elif status == "online":
            icon = f"{C.GREEN}●{C.RESET}"
            display = "idle"
        else:
            icon = f"{C.RED}●{C.RESET}"
            display = "offline"

        print(f"  {icon} {name:<25} [{display}]  {C.DIM}{labels}{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _list_issues():
    """List open issues across all repos."""
    repos = _api_paginated("/user/repos?sort=updated&direction=desc", limit=50)
    if not repos:
        warn("No repositories found.")
        return

    all_issues = []
    for repo in repos:
        full_name = repo.get("full_name", "")
        if not repo.get("has_issues"):
            continue
        issues = _api_paginated(f"/repos/{full_name}/issues?state=open", limit=10)
        if issues:
            for issue in issues:
                if "pull_request" in issue:
                    continue
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
    """Create a new GitHub repository."""
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
    }

    if init:
        gitignore_idx = pick_option("Add .gitignore template?", [
            "None", "Python", "Go", "Node", "Rust", "Java", "Cancel",
        ])
        gitignore_map = {1: "Python", 2: "Go", 3: "Node", 4: "Rust", 5: "Java"}
        if gitignore_idx == 6:
            return
        if gitignore_idx in gitignore_map:
            data["gitignore_template"] = gitignore_map[gitignore_idx]

        license_idx = pick_option("Add license?", [
            "None", "MIT", "Apache-2.0", "GPL-3.0", "AGPL-3.0", "Cancel",
        ])
        license_map = {1: "mit", 2: "apache-2.0", 3: "gpl-3.0", 4: "agpl-3.0"}
        if license_idx == 5:
            return
        if license_idx in license_map:
            data["license_template"] = license_map[license_idx]

    result = _api("/user/repos", method="POST", data=data)
    if result:
        full_name = result.get("full_name", name)
        clone_url = result.get("clone_url", "")
        log_action("GitHub Create Repo", full_name)
        success(f"Created repository: {full_name}")
        if clone_url and confirm("Clone to local machine?"):
            _clone_repo(result)
    else:
        error("Failed to create repository.")


def _clone_repo(repo):
    """Clone a GitHub repository to localhost."""
    clone_url = repo.get("clone_url", "")
    ssh_url = repo.get("ssh_url", "")
    full_name = repo.get("full_name", "?")

    if not clone_url and not ssh_url:
        error("No clone URL available.")
        return

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
            log_action("GitHub Clone Repo", f"{full_name} → {target}")
            success(f"Cloned to {target}")
        else:
            error(f"Git clone failed: {result.stderr.strip()}")
    except FileNotFoundError:
        error("git not found. Install git to use this feature.")
    except subprocess.TimeoutExpired:
        error("Clone timed out.")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Pull Requests ────────────────────────────────────────────────────────

def _list_pull_requests():
    """List open pull requests across all repos."""
    repos = _api_paginated("/user/repos?sort=updated&direction=desc", limit=50)
    if not repos:
        warn("No repositories found.")
        return

    all_prs = []
    for repo in repos:
        full_name = repo.get("full_name", "")
        prs = _api_paginated(f"/repos/{full_name}/pulls?state=open", limit=10)
        if prs:
            for pr in prs:
                pr["_repo"] = full_name
                all_prs.append(pr)

    if not all_prs:
        info("No open pull requests across any repository.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for pr in all_prs:
            repo_name = pr.get("_repo", "?")
            num = pr.get("number", "?")
            title = pr.get("title", "?")[:40]
            author = pr.get("user", {}).get("login", "?")
            head = pr.get("head", {}).get("ref", "?")
            choices.append(
                f"{repo_name} #{num}  {title}  "
                f"{C.DIM}{author} ({head}){C.RESET}"
            )

        choices.append("← Back")
        idx = pick_option(f"Open Pull Requests ({len(all_prs)}):", choices)
        if idx >= len(all_prs):
            return

        _pr_detail(all_prs[idx])


def _pr_detail(pr):
    """Show detail and actions for a pull request."""
    repo = pr.get("_repo", "?")
    num = pr.get("number", "?")
    title = pr.get("title", "?")
    body = (pr.get("body") or "")[:200]
    author = pr.get("user", {}).get("login", "?")
    head = pr.get("head", {}).get("ref", "?")
    base_ref = pr.get("base", {}).get("ref", "?")
    mergeable = pr.get("mergeable", None)
    created = pr.get("created_at", "?")[:10]

    print(f"\n  {C.BOLD}PR #{num}: {title}{C.RESET}")
    print(f"  Repo: {repo}")
    print(f"  Author: {author}")
    print(f"  Branch: {head} → {base_ref}")
    print(f"  Created: {created}")
    if mergeable is not None:
        merge_str = (f"{C.GREEN}mergeable{C.RESET}" if mergeable
                     else f"{C.RED}conflicts{C.RESET}")
        print(f"  Status: {merge_str}")
    if body:
        print(f"\n  {C.DIM}{body}{C.RESET}")

    labels = pr.get("labels", [])
    if labels:
        label_str = ", ".join(la.get("name", "") for la in labels)
        print(f"  Labels: {label_str}")

    aidx = pick_option(f"PR #{num}:", [
        "Merge PR",
        "Close PR",
        "← Back",
    ])
    if aidx == 2:
        return
    elif aidx == 0:
        if confirm(f"Merge PR #{num} '{title}'?", default_yes=False):
            result = _api(
                f"/repos/{repo}/pulls/{num}/merge", method="PUT",
                data={"merge_method": "merge", "commit_title": title})
            if result is not None:
                log_action("GitHub Merge PR", f"{repo} #{num}")
                success(f"Merged PR #{num}")
            else:
                error("Merge failed.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif aidx == 1:
        if confirm(f"Close PR #{num} without merging?", default_yes=False):
            result = _api(
                f"/repos/{repo}/pulls/{num}", method="PATCH",
                data={"state": "closed"})
            if result is not None:
                log_action("GitHub Close PR", f"{repo} #{num}")
                success(f"Closed PR #{num}")
            else:
                error("Close failed.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Actions Runs ────────────────────────────────────────────────────────

def _repo_ci_runs(full_name):
    """Show recent GitHub Actions workflow runs for a repository."""
    runs_data = _api(f"/repos/{full_name}/actions/runs?per_page=20")
    if not runs_data:
        info("No Actions runs found (Actions may not be configured).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    run_list = runs_data.get("workflow_runs", [])
    if not run_list:
        info("No Actions runs found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Actions Runs — {full_name}{C.RESET}\n")
    for run in run_list[:20]:
        status = run.get("status", "?")
        conclusion = run.get("conclusion", "")
        name = run.get("name", "?")
        event = run.get("event", "?")
        created = run.get("created_at", "?")[:16]

        if conclusion == "success":
            icon = f"{C.GREEN}●{C.RESET}"
        elif conclusion == "failure":
            icon = f"{C.RED}●{C.RESET}"
        elif status in ("in_progress", "queued", "waiting"):
            icon = f"{C.YELLOW}●{C.RESET}"
        else:
            icon = f"{C.DIM}○{C.RESET}"

        display_status = conclusion or status
        print(f"  {icon} {name:<30} [{display_status}]  {C.DIM}{event} @ {created}{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Webhooks ─────────────────────────────────────────────────────────────

def _repo_webhooks(full_name):
    """List, create, and delete webhooks for a repository."""
    while True:
        hooks = _api_paginated(f"/repos/{full_name}/hooks", limit=30)
        if hooks is None:
            error("Could not fetch webhooks (insufficient permissions?).")
            return
        if not isinstance(hooks, list):
            hooks = []

        choices = []
        for h in hooks:
            url = h.get("config", {}).get("url", "?")[:40]
            active = h.get("active", False)
            events = ", ".join(h.get("events", []))[:30]
            icon = f"{C.GREEN}●{C.RESET}" if active else f"{C.RED}●{C.RESET}"
            choices.append(f"{icon} {url:<42} {C.DIM}events: {events}{C.RESET}")

        choices.extend([
            "───────────────",
            "+ Create Webhook",
            "← Back",
        ])

        idx = pick_option(f"Webhooks: {full_name} ({len(hooks)}):", choices)

        if choices[idx] == "← Back":
            return
        elif choices[idx].startswith("───"):
            continue
        elif "+ Create Webhook" in choices[idx]:
            _create_webhook(full_name)
        elif idx < len(hooks):
            _webhook_actions(full_name, hooks[idx])


def _create_webhook(full_name):
    """Create a new webhook for a repository."""
    url = prompt_text("Webhook URL:")
    if not url:
        return

    event_idx = pick_option("Trigger events:", [
        "push only",
        "push + pull_request",
        "all events",
        "← Back",
    ])
    if event_idx == 3:
        return

    events_map = {
        0: ["push"],
        1: ["push", "pull_request"],
    }
    events = events_map.get(event_idx, ["push"])

    hook_data = {
        "config": {"url": url, "content_type": "json"},
        "events": events if event_idx != 2 else ["*"],
        "active": True,
    }
    result = _api(f"/repos/{full_name}/hooks", method="POST", data=hook_data)
    if result:
        log_action("GitHub Create Webhook", f"{full_name} → {url}")
        success(f"Webhook created: {url}")
    else:
        error("Failed to create webhook.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _webhook_actions(full_name, hook):
    """Show actions for a webhook (test, toggle, delete)."""
    hid = hook.get("id", "?")
    url = hook.get("config", {}).get("url", "?")
    active = hook.get("active", False)

    print(f"\n  {C.BOLD}Webhook #{hid}{C.RESET}")
    print(f"  URL: {url}")
    print(f"  Active: {'Yes' if active else 'No'}")
    print(f"  Events: {', '.join(hook.get('events', []))}")

    aidx = pick_option(f"Webhook #{hid}:", [
        "Test webhook",
        "Toggle active/inactive",
        "Delete webhook",
        "← Back",
    ])
    if aidx == 3:
        return
    elif aidx == 0:
        result = _api(f"/repos/{full_name}/hooks/{hid}/tests", method="POST")
        if result is not None:
            success("Test webhook triggered.")
        else:
            error("Test failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif aidx == 1:
        new_active = not active
        result = _api(
            f"/repos/{full_name}/hooks/{hid}", method="PATCH",
            data={"active": new_active})
        if result:
            state_str = "activated" if new_active else "deactivated"
            log_action("GitHub Webhook Toggle", f"{full_name} #{hid} {state_str}")
            success(f"Webhook {state_str}.")
        else:
            error("Toggle failed.")
    elif aidx == 2:
        if confirm(f"Delete webhook #{hid}?", default_yes=False):
            result = _api(f"/repos/{full_name}/hooks/{hid}", method="DELETE")
            if result is not None:
                log_action("GitHub Delete Webhook", f"{full_name} #{hid}")
                success("Webhook deleted.")
            else:
                error("Delete failed.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Repo Settings ────────────────────────────────────────────────────────

def _repo_settings(full_name, repo):
    """Edit repository settings (visibility, description, archive)."""
    while True:
        is_private = repo.get("private", False)
        is_archived = repo.get("archived", False)
        desc = repo.get("description") or ""

        print(f"\n  {C.BOLD}Settings: {full_name}{C.RESET}")
        print(f"  Visibility: {'Private' if is_private else 'Public'}")
        print(f"  Archived: {'Yes' if is_archived else 'No'}")
        print(f"  Description: {desc or '(none)'}")

        vis_label = "Private" if is_private else "Public"
        arch_label = "Unarchive" if is_archived else "Archive"

        idx = pick_option(f"Settings: {full_name}", [
            f"Toggle visibility      — currently {vis_label}",
            "Edit description",
            f"{arch_label} repository",
            "← Back",
        ])
        if idx == 3:
            return
        elif idx == 0:
            new_private = not is_private
            new_label = "private" if new_private else "public"
            if confirm(f"Make {full_name} {new_label}?", default_yes=False):
                result = _api(
                    f"/repos/{full_name}", method="PATCH",
                    data={"private": new_private})
                if result:
                    repo["private"] = new_private
                    log_action("GitHub Repo Visibility", f"{full_name} → {new_label}")
                    success(f"Repository is now {new_label}.")
                else:
                    error("Failed to update visibility.")
        elif idx == 1:
            new_desc = prompt_text("Description:", default=desc)
            if new_desc is not None:
                result = _api(
                    f"/repos/{full_name}", method="PATCH",
                    data={"description": new_desc})
                if result:
                    repo["description"] = new_desc
                    log_action("GitHub Repo Description", full_name)
                    success("Description updated.")
                else:
                    error("Failed to update description.")
        elif idx == 2:
            new_archived = not is_archived
            action = "archive" if new_archived else "unarchive"
            if confirm(f"{action.title()} {full_name}?", default_yes=False):
                result = _api(
                    f"/repos/{full_name}", method="PATCH",
                    data={"archived": new_archived})
                if result:
                    repo["archived"] = new_archived
                    log_action(f"GitHub Repo {action.title()}", full_name)
                    success(f"Repository {action}d.")
                else:
                    error(f"Failed to {action}.")
