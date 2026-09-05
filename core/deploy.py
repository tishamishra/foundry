"""
Foundry — deploy targets.

Three ways out of `dist/`, and one rule that governs all three:

    A SITE THAT IS NOT SHIPPED DOES NOT DEPLOY.

"Shipped" already means something exact here — it passed the QA gate AND the
SEO gate in the same build. Deploy does not re-litigate that; it refuses to
run when the record is missing. The reason is that the two gates exist to stop
a specific failure that only becomes expensive after publication: a thousand
near-duplicate pages that a search engine has already crawled cannot be
un-crawled by fixing the generator afterwards.

The targets:

    github      push dist/<domain> into a repo. Real git history, so the
                second deploy of a 26,000-page site pushes the diff, not
                26,000 files. Serves via GitHub Pages, or feeds Cloudflare
                Pages / Netlify as the source of truth.

    cloudflare  wrangler. If the build emitted a Worker, this deploys the
                Worker and its assets together — that is the only target
                that can serve the edge-rendered long tail. Otherwise it
                deploys as Pages.

    server      rsync over SSH to your own box. --delete is on, which is why
                the target path is validated harder than anything else here.

Three properties worth stating because they were deliberate:

    PREFLIGHT REFUSES, IT DOES NOT WARN.  Every check that can be made without
    touching the network is made before the first command runs: built, shipped,
    not stale, target configured, tool installed, secret present, host able to
    serve this render mode. A deploy that is going to fail should fail in
    milliseconds and say why, not halfway through an upload.

    NOTHING IS ECHOED THAT SHOULD NOT BE.  git and wrangler both print URLs and
    arguments back at you, tokens included. Every command line and every line
    of output passes through a redactor built from the current secrets before
    it is stored or displayed.

    DRY RUN IS A FIRST-CLASS MODE.  It runs the whole preflight and prints the
    exact commands, in order, without executing any of them. Use it once per
    new target; it costs nothing and it is the only way to see the rsync
    --delete line before it runs rather than after.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .graph import FoundryError, load_graph
from .verify import load_shipped

TARGETS = ("github", "cloudflare", "server")

CONFIG_PATH = ("data", "deploy.yaml")
SECRETS_PATH = ("data", "secrets.yaml")

# Secrets are global — one GitHub account, one Cloudflare account, one server.
# Per-site configuration is not secret and lives in deploy.yaml next to it.
SECRET_KEYS = {
    "github_token": "GitHub personal access token (repo scope)",
    "cloudflare_api_token": "Cloudflare API token (Workers Scripts: Edit)",
    "cloudflare_account_id": "Cloudflare account ID",
}

COMMIT_NAME = "Foundry"
COMMIT_EMAIL = "foundry@localhost"

# Long enough for a first push of a large site on a slow line, short enough
# that a hung credential prompt does not wedge the panel forever.
TIMEOUT = 1800


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    cmd: str                       # already redacted, display-safe
    ok: bool = True
    seconds: float = 0.0
    output: str = ""               # already redacted, tail only
    skipped: str = ""              # reason, when a step was allowed to fail


@dataclass
class DeployResult:
    site_id: str
    target: str
    dry_run: bool = False
    ok: bool = False
    steps: list[Step] = field(default_factory=list)
    url: str = ""
    error: str = ""
    # The FoundryError ctx from preflight, verbatim. core/verify.diagnose keys off
    # these flags, so the CLI and the panel get the same explanation from the same
    # place — neither re-derives the cause by matching on the message text.
    ctx: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def failed_step(self) -> Step | None:
        return next((s for s in self.steps if not s.ok), None)


# --------------------------------------------------------------------------
# configuration and secrets
# --------------------------------------------------------------------------

def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(root: Path) -> dict:
    return _read(root.joinpath(*CONFIG_PATH))


def save_config(root: Path, cfg: dict) -> None:
    path = root.joinpath(*CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")


def site_config(root: Path, site_id: str, target: str) -> dict:
    return ((load_config(root).get(site_id) or {}).get(target) or {})


def load_secrets(root: Path) -> dict:
    """Environment wins over the file, so a CI run never needs the file at all."""
    data = {k: str(v) for k, v in _read(root.joinpath(*SECRETS_PATH)).items() if v}
    for key in SECRET_KEYS:
        env = os.environ.get(key.upper())
        if env:
            data[key] = env
    return data


def save_secrets(root: Path, values: dict) -> None:
    """Merge and write 0600. An empty string clears a key rather than blanking it,
    because a form that posts empty fields must not silently wipe credentials."""
    path = root.joinpath(*SECRETS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: str(v) for k, v in _read(path).items() if v}
    for key, value in values.items():
        if key not in SECRET_KEYS:
            continue
        value = (value or "").strip()
        if value == "__clear__":
            data.pop(key, None)
        elif value:
            data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    _gitignore(path.parent.parent)


def _gh_api(method: str, url: str, token: str, body: dict | None = None):
    """A tiny GitHub REST call — no dependency. Returns (status, json)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "foundry-deploy")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        raise FoundryError(f"Could not reach GitHub: {e.reason}", {"target": "github"})


def _repo_name(domain: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "-", (domain or "").strip().lower()).strip("-.")
    return name or "site"


def ensure_github_repo(root: Path, site_id: str, private: bool = True) -> dict:
    """One-click prerequisite: make sure a GitHub repo exists for this site and
    record it in deploy.yaml, so a plain `deploy(github)` can push straight to it.
    Creates the repo (named after the domain) under the token's account if absent."""
    token = load_secrets(root).get("github_token")
    if not token:
        raise FoundryError(
            "No GitHub token saved. Add a GitHub personal-access token with 'repo' "
            "scope under Deploy → Credentials (or set GITHUB_TOKEN in the environment), "
            "then click Deploy to GitHub again.",
            {"target_unconfigured": True, "target": "github"})

    site = _read(root / "data" / "sites" / f"{site_id}.yaml")
    domain = (site.get("domain") or site_id).strip().lower()
    repo = _repo_name(domain)

    status, me = _gh_api("GET", "https://api.github.com/user", token)
    if status != 200 or not me.get("login"):
        raise FoundryError(
            f"GitHub rejected the token (HTTP {status}). Check it is valid and has "
            f"'repo' scope.", {"target": "github"})
    owner = me["login"]

    status, _info = _gh_api("GET", f"https://api.github.com/repos/{owner}/{repo}", token)
    created = False
    if status == 404:
        status, resp = _gh_api(
            "POST", "https://api.github.com/user/repos", token,
            {"name": repo, "private": private, "auto_init": False,
             "description": f"{domain} — built with Foundry"})
        if status not in (200, 201):
            raise FoundryError(
                f"Could not create the GitHub repo (HTTP {status}): "
                f"{resp.get('message', 'unknown error')}", {"target": "github"})
        created = True
    elif status != 200:
        raise FoundryError(
            f"GitHub API error checking the repo (HTTP {status}).", {"target": "github"})

    repo_url = f"https://github.com/{owner}/{repo}.git"
    cfg = load_config(root)
    g = cfg.setdefault(site_id, {}).setdefault("github", {})
    g["repo"] = repo_url
    g.setdefault("branch", "main")
    g.setdefault("pages", True)
    save_config(root, cfg)
    return {"owner": owner, "repo": repo, "url": repo_url, "created": created,
            "private": private, "web": f"https://github.com/{owner}/{repo}"}


def _gitignore(root: Path) -> None:
    """The foundry folder may itself be a repo. Never let the secrets file in."""
    path = root / ".gitignore"
    want = ["data/secrets.yaml", "dist/", ".deploy/", ".foundry-trash-*", "node_modules/"]
    have = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [w for w in want if w not in have]
    if missing:
        path.write_text("\n".join(have + missing).strip() + "\n", encoding="utf-8")


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def redactor(secrets: dict) -> Any:
    """git and wrangler echo their own arguments. Everything stored or shown
    goes through this first — there is no second place to remember to do it.

    It also strips ANSI colour, because wrangler assumes a terminal and its
    escape sequences render in a browser as literal `[33m` noise wrapped around
    the one line you needed to read."""
    values = sorted((v for v in secrets.values() if v and len(v) > 6), key=len,
                    reverse=True)

    def scrub(text: str) -> str:
        for value in values:
            text = text.replace(value, "••••••")
        return ANSI.sub("", text)
    return scrub


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------

def _newest(path: Path, limit: int = 4000) -> float:
    """Newest mtime in a tree, sampling wide trees. Exact enough for 'is the
    build older than the config', which is all it is asked."""
    newest, seen = 0.0, 0
    for child in path.rglob("*"):
        try:
            newest = max(newest, child.stat().st_mtime)
        except OSError:
            continue
        seen += 1
        if seen >= limit:
            break
    return newest


def preflight(root: Path, site_id: str, target: str) -> dict:
    """Everything knowable without the network. Raises FoundryError with a ctx
    key that core/verify.diagnose turns into a cause and a fix."""
    if target not in TARGETS:
        raise FoundryError(f"Unknown deploy target {target!r}. Known: {', '.join(TARGETS)}",
                           {"unknown_target": True})

    graph = load_graph(root, site_id)          # raises on its own for bad sites
    domain = graph.site["domain"]
    dist = root / "dist" / domain

    if not (dist / "index.html").is_file():
        raise FoundryError(f"{site_id} has not been built — there is nothing in "
                           f"dist/{domain} to deploy.", {"not_built": True})

    if site_id not in load_shipped(root):
        raise FoundryError(
            f"{site_id} is not shipped. It built, but it did not pass both the QA "
            f"gate and the SEO gate, so it is previewable and not deployable.",
            {"not_shipped": True})

    src = root / "data" / "sites" / f"{site_id}.yaml"
    if src.is_file() and src.stat().st_mtime > _newest(dist):
        raise FoundryError(
            f"{site_id} was edited after its last build. Deploying now would "
            f"publish the previous version.", {"stale_build": True})

    cfg = site_config(root, site_id, target)
    if not cfg:
        raise FoundryError(f"No {target} settings for {site_id}.",
                           {"target_unconfigured": True, "target": target})

    secrets = load_secrets(root)
    worker = (dist / "worker.js").is_file()

    checks = {"github": _pre_github, "cloudflare": _pre_cloudflare,
              "server": _pre_server}[target]
    notes = checks(cfg, secrets, worker, domain)

    return {"graph": graph, "domain": domain, "dist": dist, "cfg": cfg,
            "secrets": secrets, "worker": worker, "notes": notes}


def _need_tool(name: str, install: str) -> None:
    if not shutil.which(name):
        raise FoundryError(f"{name} is not installed, so this target cannot run. {install}",
                           {"missing_tool": True, "tool": name})


def _static_only(worker: bool, host: str) -> None:
    if worker:
        raise FoundryError(
            f"This site renders its long tail at the edge, and {host} serves static "
            f"files only — every city page outside the prerendered set would 404. "
            f"Deploy it to Cloudflare, or set render.prerender_top_n to null and "
            f"rebuild so every page is a real file.",
            {"edge_on_static_host": True, "host": host})


def _pre_github(cfg: dict, secrets: dict, worker: bool, domain: str) -> list[str]:
    _need_tool("git", "Install the Xcode command line tools: xcode-select --install")
    if not cfg.get("repo"):
        raise FoundryError("The github target needs a repo URL.",
                           {"target_unconfigured": True, "target": "github"})
    notes = []
    if cfg.get("pages", True):
        _static_only(worker, "GitHub Pages")
        notes.append(f"CNAME will be written as {domain} and .nojekyll added — "
                     f"without .nojekyll, GitHub Pages hides every _masters/ path "
                     f"and any directory beginning with an underscore.")
    url = str(cfg["repo"])
    if url.startswith("https://") and not secrets.get("github_token"):
        notes.append("No github_token saved. An https push will prompt for "
                     "credentials, and nothing is watching the prompt — save a "
                     "token, or use an ssh:// remote.")
    return notes


def _pre_cloudflare(cfg: dict, secrets: dict, worker: bool, domain: str) -> list[str]:
    _need_tool("npx", "Cloudflare deploys use wrangler, which needs Node. "
                      "Install Node from nodejs.org.")
    for key in ("cloudflare_api_token", "cloudflare_account_id"):
        if not secrets.get(key):
            raise FoundryError(f"{SECRET_KEYS[key]} is not saved.",
                               {"missing_secret": True, "secret": key})
    mode = _cf_mode(cfg, worker)
    if mode == "pages" and not cfg.get("project"):
        raise FoundryError("Cloudflare Pages needs a project name.",
                           {"target_unconfigured": True, "target": "cloudflare"})
    if mode == "worker" and not worker:
        raise FoundryError(
            "This site was built fully static, so there is no worker.js to deploy. "
            "Use mode: pages, or set render.prerender_top_n and rebuild.",
            {"no_worker": True})
    return [f"Deploying as a {mode}."]


def _cf_mode(cfg: dict, worker: bool) -> str:
    mode = str(cfg.get("mode", "auto")).lower()
    return ("worker" if worker else "pages") if mode == "auto" else mode


def _pre_server(cfg: dict, secrets: dict, worker: bool, domain: str) -> list[str]:
    _need_tool("rsync", "Install rsync, or use a different target.")
    for key in ("host", "path"):
        if not str(cfg.get(key, "")).strip():
            raise FoundryError(f"The server target needs {key}.",
                               {"target_unconfigured": True, "target": "server"})
    _static_only(worker, "a plain web server")

    # --delete is on. These are the paths where a typo is not recoverable.
    path = str(cfg["path"]).rstrip("/") or "/"
    forbidden = {"", "/", "/etc", "/usr", "/var", "/home", "/root", "/bin", "/boot",
                 "/lib", "/opt", "/sbin", "/srv", "/sys", "/proc", "/dev", "/var/www"}
    if path in forbidden:
        raise FoundryError(
            f"Refusing to rsync --delete into {path!r}. That path is a system "
            f"directory or the filesystem root, and --delete removes everything "
            f"there that is not in dist/. Use a directory owned by the site, "
            f"e.g. /var/www/{domain}.",
            {"dangerous_path": True, "path": path})
    if cfg.get("key") and not Path(str(cfg["key"])).expanduser().is_file():
        raise FoundryError(f"SSH key not found at {cfg['key']}.",
                           {"missing_key": True})
    return [f"rsync --delete into {path} — anything already there that is not part "
            f"of this build is removed."]


# --------------------------------------------------------------------------
# command plans
# --------------------------------------------------------------------------

@dataclass
class Cmd:
    name: str
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    allow_fail: str = ""           # non-empty: failure is expected and explained
    before: Any = None             # callable run just before this command


def _auth_url(url: str, token: str) -> str:
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


def plan_github(root: Path, pre: dict, site_id: str) -> tuple[list[Cmd], str]:
    cfg, dist, domain = pre["cfg"], pre["dist"], pre["domain"]
    branch = str(cfg.get("branch", "main"))
    work = root / ".deploy" / site_id / "github"
    url = _auth_url(str(cfg["repo"]), pre["secrets"].get("github_token", ""))
    fresh = not (work / ".git").is_dir()

    def sync() -> None:
        # Mirror dist/ into the worktree: clear everything except .git, then copy.
        # Copying without clearing would leave pages deleted from the build alive
        # in the repo forever, which is how stale city pages outlive their site.
        work.mkdir(parents=True, exist_ok=True)
        for child in work.iterdir():
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(dist, work, dirs_exist_ok=True)
        if cfg.get("pages", True):
            (work / ".nojekyll").write_text("", encoding="utf-8")
            (work / "CNAME").write_text(domain + "\n", encoding="utf-8")

    cmds: list[Cmd] = []
    if fresh:
        cmds += [
            Cmd("init", ["git", "init", "-q", "-b", branch], work),
            Cmd("remote", ["git", "remote", "add", "origin", url], work),
            Cmd("fetch", ["git", "fetch", "--depth", "1", "origin", branch], work,
                allow_fail="The branch does not exist yet — this is the first deploy."),
            Cmd("checkout", ["git", "reset", "--soft", "FETCH_HEAD"], work,
                allow_fail="Nothing fetched, so there is nothing to reset onto."),
        ]
    else:
        cmds.append(Cmd("remote", ["git", "remote", "set-url", "origin", url], work))

    cmds += [
        Cmd("copy", ["git", "add", "-A"], work, before=sync),
        Cmd("commit", ["git", "-c", f"user.name={COMMIT_NAME}",
                       "-c", f"user.email={COMMIT_EMAIL}", "commit",
                       "-m", f"foundry: {site_id} {time.strftime('%Y-%m-%d %H:%M')}"],
            work, allow_fail="Nothing changed since the last deploy."),
        Cmd("push", ["git", "push", "-u", "origin", branch]
                    + (["--force"] if cfg.get("force") else []), work),
    ]
    public = str(cfg.get("url") or (f"https://{domain}/" if cfg.get("pages", True) else ""))
    return cmds, public


def plan_cloudflare(root: Path, pre: dict, site_id: str) -> tuple[list[Cmd], str]:
    cfg, dist, secrets = pre["cfg"], pre["dist"], pre["secrets"]
    env = {"CLOUDFLARE_API_TOKEN": secrets["cloudflare_api_token"],
           "CLOUDFLARE_ACCOUNT_ID": secrets["cloudflare_account_id"],
           "NO_D1_WARNING": "true", "WRANGLER_SEND_METRICS": "false"}
    mode = _cf_mode(cfg, pre["worker"])
    wrangler = ["npx", "--yes", "wrangler@latest"]

    if mode == "worker":
        argv = wrangler + ["deploy"]
        url = str(cfg.get("url") or f"https://{pre['domain']}/")
    else:
        argv = wrangler + ["pages", "deploy", ".", "--project-name",
                           str(cfg["project"]), "--branch",
                           str(cfg.get("branch", "main")), "--commit-dirty=true"]
        url = str(cfg.get("url") or f"https://{cfg['project']}.pages.dev/")
    return [Cmd(mode, argv, dist, env=env)], url


def plan_server(root: Path, pre: dict, site_id: str) -> tuple[list[Cmd], str]:
    cfg, dist = pre["cfg"], pre["dist"]
    ssh = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "BatchMode=yes"]                       # never hang on a prompt
    if cfg.get("port"):
        ssh += ["-p", str(cfg["port"])]
    if cfg.get("key"):
        ssh += ["-i", str(Path(str(cfg["key"])).expanduser())]

    user = str(cfg.get("user", "")).strip()
    dest = f"{user + '@' if user else ''}{cfg['host']}:{str(cfg['path']).rstrip('/')}/"
    argv = ["rsync", "-az", "--delete", "--human-readable", "--stats",
            "-e", " ".join(ssh), f"{dist}/", dest]
    return [Cmd("rsync", argv, root)], str(cfg.get("url") or f"https://{pre['domain']}/")


PLANS = {"github": plan_github, "cloudflare": plan_cloudflare, "server": plan_server}


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def deploy(root: Path, site_id: str, target: str, dry_run: bool = False) -> DeployResult:
    started = time.time()
    result = DeployResult(site_id=site_id, target=target, dry_run=dry_run)
    try:
        pre = preflight(root, site_id, target)
    except FoundryError as exc:
        result.error = str(exc)
        result.ctx = dict(getattr(exc, "ctx", {}) or {})
        result.seconds = round(time.time() - started, 1)
        return result

    result.notes = pre["notes"]
    scrub = redactor(pre["secrets"])
    cmds, result.url = PLANS[target](root, pre, site_id)

    for cmd in cmds:
        # shlex.join, not " ".join: rsync's -e carries a whole ssh command line as
        # ONE argument, and an unquoted display of it reads as five separate flags.
        # A dry run whose whole purpose is "see the command before it runs" has to
        # print something you could paste into a shell and get the same result.
        shown = scrub(shlex.join(cmd.argv))
        if dry_run:
            result.steps.append(Step(name=cmd.name, cmd=shown, ok=True,
                                     output="(dry run — not executed)"))
            continue

        t0 = time.time()
        try:
            if cmd.before:
                cmd.before()
            cmd.cwd.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                cmd.argv, cwd=str(cmd.cwd), capture_output=True, text=True,
                timeout=TIMEOUT, env={**os.environ, **cmd.env,
                                      "GIT_TERMINAL_PROMPT": "0"})
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = f"timed out after {TIMEOUT}s", False
        except OSError as exc:
            out, ok = str(exc), False

        step = Step(name=cmd.name, cmd=shown, ok=ok or bool(cmd.allow_fail),
                    seconds=round(time.time() - t0, 1),
                    output=scrub(out)[-2500:],
                    skipped=cmd.allow_fail if not ok else "")
        result.steps.append(step)
        if not ok and not cmd.allow_fail:
            result.error = f"{cmd.name} failed"
            result.seconds = round(time.time() - started, 1)
            return result

    result.ok = True
    result.seconds = round(time.time() - started, 1)
    return result
