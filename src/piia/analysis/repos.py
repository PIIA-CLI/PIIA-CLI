"""Repository resolution and git facts.

Resolution order for every ``--prior`` / ``--target`` argument:

1. **A local path** -- used as-is.
2. **An existing checkout** found under the configured search roots, matched by
   git remote URL first and by directory name second. This is the normal case
   for developers who already have their repositories side by side on disk, and
   it means no network and no duplicate clone.
3. **A clone** into ``<workspace>/repos/<owner>/<name>``, only if the first two
   found nothing and cloning is enabled.

Which of the three happened is recorded in ``RepoRef.source`` and surfaced in
the output, because an auditor needs to know whether a claim about prior work
came from the developer's own checkout or from a fresh clone.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

from piia.analysis.models import GitFacts, RepoRef
from piia.errors import GitNotFoundError, RepositoryError

_URL_RE = re.compile(
    r"^(?:(?P<scheme>https?|git|ssh)://)?"
    r"(?:(?P<user>[^@/]+)@)?"
    r"(?P<host>[^/:]+)[:/]"
    r"(?P<path>.+?)(?:\.git)?/?$"
)
_SHORTHAND_RE = re.compile(r"^(?P<owner>[\w.\-]+)/(?P<name>[\w.\-]+)$")


def git_path() -> str:
    exe = shutil.which("git")
    if not exe:
        raise GitNotFoundError()
    return exe


def git_available() -> bool:
    return shutil.which("git") is not None


def run_git(
    args: list[str], cwd: Path | None = None, timeout: float = 120.0, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with a timeout and no interactive prompting."""
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_ASKPASS", "echo")
    try:
        proc = subprocess.run(
            [git_path(), *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(
            f"git {' '.join(args[:2])} timed out after {timeout:.0f}s.",
            remediation="Raise the timeout, or pre-clone the repository and pass its path.",
        ) from exc
    if check and proc.returncode != 0:
        raise RepositoryError(
            f"git {' '.join(args[:2])} failed: {proc.stderr.strip() or 'unknown error'}",
            details=[{"field": "argv", "issue": " ".join(args)}],
        )
    return proc


def git_version() -> str | None:
    if not git_available():
        return None
    proc = run_git(["--version"], timeout=15)
    return proc.stdout.strip().replace("git version ", "") or None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def normalize_remote(url: str | None) -> str | None:
    """Reduce any remote form to ``host/owner/name`` in lowercase.

    ``git@github.com:Acme/Repo.git``, ``https://github.com/acme/repo`` and
    ``ssh://git@github.com/acme/repo.git`` all collapse to
    ``github.com/acme/repo``.
    """
    if not url:
        return None
    text = url.strip().rstrip("/")
    if text.startswith("file://"):
        text = text[len("file://") :]
    if Path(text).expanduser().exists():
        return None
    match = _URL_RE.match(text)
    if not match:
        return None
    host = match.group("host").lower()
    path = match.group("path").strip("/").lower()
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}"


def parse_repo_spec(spec: str, *, role: str = "prior") -> RepoRef:
    """Turn one CLI argument into a :class:`RepoRef` (unresolved)."""
    raw = spec.strip()
    if not raw:
        raise RepositoryError("Empty repository specification.")

    candidate = Path(raw).expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        remote = read_origin(resolved)
        norm = normalize_remote(remote)
        owner = norm.split("/")[1] if norm and norm.count("/") >= 2 else None
        return RepoRef(
            name=resolved.name,
            url=remote,
            host=norm.split("/")[0] if norm else None,
            owner=owner,
            path=str(resolved),
            source="local-path",
            remote_url=remote,
            role=role,
        )

    short = _SHORTHAND_RE.match(raw)
    if short and "." not in short.group("owner"):
        owner, name = short.group("owner"), short.group("name")
        return RepoRef(
            name=name,
            url=f"https://github.com/{owner}/{name}",
            host="github.com",
            owner=owner,
            source="pending",
            remote_url=f"https://github.com/{owner}/{name}",
            role=role,
        )

    match = _URL_RE.match(raw)
    if not match:
        raise RepositoryError(
            f"Could not parse repository specification {spec!r}.",
            details=[{"field": "repository", "issue": "not a path, URL, or owner/name"}],
            remediation=(
                "Pass a local path, a clone URL "
                "(https://github.com/owner/name), or owner/name shorthand."
            ),
        )
    host = match.group("host").lower()
    parts = [p for p in match.group("path").strip("/").split("/") if p]
    name = (parts[-1] if parts else "repository").removesuffix(".git")
    owner = parts[-2] if len(parts) >= 2 else None
    return RepoRef(
        name=name,
        url=raw.removesuffix(".git"),
        host=host,
        owner=owner,
        source="pending",
        remote_url=raw,
        role=role,
    )


def read_origin(path: Path) -> str | None:
    """Read ``remote.origin.url`` from ``.git/config`` without spawning git."""
    for remote in read_remotes(path):
        return remote
    return None


def read_remotes(path: Path) -> list[str]:
    """Every remote URL configured in a checkout, ``origin`` first."""
    git_dir = path / ".git"
    config_path: Path | None = None
    if git_dir.is_dir():
        config_path = git_dir / "config"
    elif git_dir.is_file():  # worktree or submodule: '.git' is a pointer file
        try:
            pointer = git_dir.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return []
        if pointer.startswith("gitdir:"):
            target = Path(pointer.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = (path / target).resolve()
            config_path = target / "config"
            if not config_path.is_file():
                config_path = target.parent.parent / "config"
    if not config_path or not config_path.is_file():
        return []
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(config_path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return []
    remotes: list[str] = []
    for section in parser.sections():
        if section.startswith('remote "') and parser.has_option(section, "url"):
            url = parser.get(section, "url")
            if section == 'remote "origin"':
                remotes.insert(0, url)
            else:
                remotes.append(url)
    return remotes


# ---------------------------------------------------------------------------
# Local discovery
# ---------------------------------------------------------------------------
class LocalIndex:
    """An index of the git checkouts under a set of roots.

    Built once per run by reading ``.git/config`` files directly, which is fast
    enough to scan a few hundred sibling directories without spawning git.
    """

    def __init__(self, roots: list[str], depth: int = 2) -> None:
        self.roots = [Path(r).expanduser() for r in roots]
        self.depth = max(1, depth)
        self.by_remote: dict[str, Path] = {}
        self.by_name: dict[str, list[Path]] = {}
        self._build()

    def _build(self) -> None:
        seen: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in self._walk(root, self.depth):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                for remote in read_remotes(resolved):
                    norm = normalize_remote(remote)
                    if norm:
                        self.by_remote.setdefault(norm, resolved)
                self.by_name.setdefault(resolved.name.lower(), []).append(resolved)

    def _walk(self, root: Path, depth: int) -> list[Path]:
        found: list[Path] = []
        if (root / ".git").exists():
            found.append(root)
        if depth <= 0:
            return found
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except (PermissionError, OSError):
            return found
        for entry in entries:
            if entry.name in {".git", "node_modules", "__pycache__", ".venv"}:
                continue
            found.extend(self._walk(entry, depth - 1))
        return found

    def lookup(self, ref: RepoRef) -> tuple[Path | None, str]:
        """Find ``ref`` locally. Returns ``(path, match_kind)``."""
        norm = normalize_remote(ref.remote_url or ref.url)
        if norm and norm in self.by_remote:
            return self.by_remote[norm], "remote-url"
        if norm:
            # Same owner/name on a different host (a mirror or a fork).
            tail = "/".join(norm.split("/")[1:])
            for known, path in self.by_remote.items():
                if "/".join(known.split("/")[1:]) == tail:
                    return path, "remote-path"
        candidates = self.by_name.get(ref.name.lower(), [])
        if len(candidates) == 1:
            return candidates[0], "directory-name"
        if candidates:
            # Prefer a candidate whose remote at least mentions the owner.
            if ref.owner:
                for path in candidates:
                    remotes = " ".join(read_remotes(path)).lower()
                    if ref.owner.lower() in remotes:
                        return path, "directory-name"
            return candidates[0], "directory-name-ambiguous"
        return None, "none"

    @property
    def size(self) -> int:
        return len(self.by_name)


def resolve(
    ref: RepoRef,
    *,
    index: LocalIndex | None = None,
    workspace: Path | None = None,
    clone_missing: bool = True,
    clone_depth: int | None = None,
) -> tuple[RepoRef, str | None]:
    """Resolve ``ref`` to a local directory. Returns ``(ref, note)``."""
    if ref.path and Path(ref.path).is_dir():
        return ref, None

    if index is not None:
        path, kind = index.lookup(ref)
        if path is not None:
            ref.path = str(path)
            ref.source = "local-discovery"
            ref.remote_url = ref.remote_url or read_origin(path)
            note = f"{ref.name}: using existing local checkout at {path} (matched by {kind})"
            if kind == "directory-name-ambiguous":
                note += " -- several directories share this name; pass an explicit path to pin it"
            return ref, note

    if not clone_missing:
        raise RepositoryError(
            f"No local checkout found for {ref.slug} and cloning is disabled.",
            details=[{"field": "repository", "issue": f"{ref.url or ref.name} not found locally"}],
            remediation=(
                "Pass --search-root pointing at the directory that holds your checkouts, "
                "give an explicit path, or drop --no-clone-missing to allow cloning."
            ),
        )
    if not ref.remote_url:
        raise RepositoryError(
            f"{ref.name} was not found locally and has no clone URL.",
            remediation="Pass a path to the checkout, or a full clone URL.",
        )
    target = clone(ref, workspace or Path("workspace"), depth=clone_depth)
    ref.path = str(target)
    ref.source = "clone"
    return ref, f"{ref.name}: cloned into {target} (no local checkout found)"


def clone(ref: RepoRef, workspace: Path, *, depth: int | None = None) -> Path:
    """Clone ``ref`` into the workspace, reusing an existing clone if present."""
    dest_parent = workspace / "repos" / (ref.owner or "_")
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest = dest_parent / ref.name
    if (dest / ".git").exists():
        run_git(["fetch", "--quiet", "--all"], cwd=dest, timeout=300)
        return dest
    args = ["clone", "--quiet", "--no-tags"]
    if depth:
        args += ["--depth", str(depth)]
    args += [str(ref.remote_url), str(dest)]
    proc = run_git(args, timeout=900)
    if proc.returncode != 0:
        stderr = proc.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else "unknown error"
        raise RepositoryError(
            f"Could not clone {ref.remote_url}: {detail}",
            details=[{"field": "repository", "issue": detail}],
            remediation=(
                "Check the URL and your credentials. For private repositories, "
                "authenticate first (e.g. 'gh auth login' or an SSH key), or pass a "
                "local path instead."
            ),
        )
    return dest


# ---------------------------------------------------------------------------
# Git facts
# ---------------------------------------------------------------------------
def read_git_facts(path: Path, *, history_limit: int = 2000) -> GitFacts:
    """Collect commit, date range, contributor and tag facts from a checkout."""
    if not (path / ".git").exists():
        return GitFacts(is_git_repo=False)

    facts = GitFacts()
    head = run_git(["rev-parse", "HEAD"], cwd=path, timeout=30)
    if head.returncode == 0:
        facts.commit = head.stdout.strip() or None
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, timeout=30)
    if branch.returncode == 0:
        facts.branch = branch.stdout.strip() or None
    facts.is_shallow = (path / ".git" / "shallow").exists()

    log = run_git(
        ["log", f"--max-count={history_limit}", "--pretty=format:%aI%x09%aN"],
        cwd=path,
        timeout=180,
    )
    if log.returncode == 0 and log.stdout.strip():
        dates: list[str] = []
        authors: dict[str, int] = {}
        for line in log.stdout.splitlines():
            iso, _, author = line.partition("\t")
            if iso:
                dates.append(iso.strip())
            if author.strip():
                authors[author.strip()] = authors.get(author.strip(), 0) + 1
        if dates:
            facts.last_commit_date = dates[0][:10]
            facts.first_commit_date = dates[-1][:10]
        facts.contributors = [a for a, _ in sorted(authors.items(), key=lambda kv: -kv[1])]

    count = run_git(["rev-list", "--count", "HEAD"], cwd=path, timeout=60)
    if count.returncode == 0 and count.stdout.strip().isdigit():
        facts.commit_count = int(count.stdout.strip())

    tags = run_git(["tag", "--sort=-creatordate"], cwd=path, timeout=30)
    if tags.returncode == 0:
        facts.tags = [t.strip() for t in tags.stdout.splitlines() if t.strip()][:10]

    return facts
