"""The built-in deterministic scanner.

This is the floor of the tool: it needs nothing but a filesystem, so an
analysis always succeeds even with no ``repowise``, no network and no model.
Every technology it reports is traceable to a named file or a named dependency
(see ``signatures.py``); nothing is inferred.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piia.analysis.models import Dependency, LanguageStat, TechInventory
from piia.analysis.signatures import (
    BINARY_EXTENSIONS,
    DEPENDENCY_SIGNATURES,
    DIR_SIGNATURES,
    EXTENSION_LANGUAGES,
    FILE_SIGNATURES,
    IGNORED_DIRS,
    MANIFESTS,
    SPDX_HINTS,
)

README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
LICENSE_NAMES = ("license", "license.md", "license.txt", "licence", "licence.md", "copying")
ENTRYPOINT_NAMES = (
    "main.py",
    "app.py",
    "cli.py",
    "server.py",
    "run.py",
    "manage.py",
    "__main__.py",
    "index.js",
    "index.ts",
    "main.go",
    "main.rs",
)
MAX_MANIFEST_BYTES = 512_000
MAX_README_BYTES = 200_000
READ_EXCERPT_CHARS = 1600


@dataclass
class ScanResult:
    technology: TechInventory
    metrics: dict[str, Any]
    license: str | None = None
    description: str | None = None
    readme_excerpt: str | None = None
    notes: list[str] = field(default_factory=list)


def scan_repository(
    path: Path,
    *,
    max_files: int = 60000,
    exclude: list[str] | None = None,
) -> ScanResult:
    """Inventory one checkout. Deterministic for a given working tree."""
    root = Path(path)
    exclude = exclude or []
    tech = TechInventory()
    buckets: dict[str, set[str]] = {cat: set() for cat in TechInventory.CATEGORIES}
    lang_files: dict[str, int] = {}
    lang_bytes: dict[str, int] = {}
    dependencies: dict[str, Dependency] = {}
    manifest_paths: list[str] = []
    entrypoints: set[str] = set()
    notes: list[str] = []

    total_files = 0
    analyzed_files = 0
    total_bytes = 0
    truncated = False
    top_level_dirs: list[str] = []
    readme_path: Path | None = None
    license_path: Path | None = None

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not d.startswith(".venv")
            and not _excluded(_join(rel_dir, d), exclude)
        )
        if rel_dir == ".":
            top_level_dirs = list(dirnames)
        for signature, (category, label) in DIR_SIGNATURES.items():
            if rel_dir == signature or rel_dir.endswith("/" + signature):
                buckets[category].add(label)

        for filename in sorted(filenames):
            total_files += 1
            if total_files > max_files:
                truncated = True
                break
            rel_path = _join(rel_dir, filename)
            if _excluded(rel_path, exclude):
                continue
            lower = filename.lower()
            full = Path(dirpath) / filename
            try:
                size = full.stat().st_size
            except OSError:
                continue
            total_bytes += size

            ext = full.suffix.lower()
            language = EXTENSION_LANGUAGES.get(ext)
            if language and ext not in BINARY_EXTENSIONS:
                lang_files[language] = lang_files.get(language, 0) + 1
                lang_bytes[language] = lang_bytes.get(language, 0) + size
                analyzed_files += 1

            _match_file_signature(lower, rel_path, buckets)

            if lower in ENTRYPOINT_NAMES and rel_dir.count("/") <= 2:
                entrypoints.add(rel_path)

            if filename in MANIFESTS and size <= MAX_MANIFEST_BYTES:
                ecosystem, manager = MANIFESTS[filename]
                buckets["package_managers"].add(manager)
                manifest_paths.append(rel_path)
                for dep in _parse_manifest(full, filename, ecosystem, rel_path):
                    dependencies.setdefault(dep.key, dep)

            if readme_path is None and lower in README_NAMES and rel_dir == ".":
                readme_path = full
            if license_path is None and lower in LICENSE_NAMES and rel_dir == ".":
                license_path = full
        if truncated:
            break

    if truncated:
        notes.append(
            f"File walk stopped at the {max_files} file cap; "
            "technology detection may be incomplete. Raise PIIA_MAX_FILES to scan more."
        )

    # Dependencies imply technologies.
    for dep in dependencies.values():
        hit = DEPENDENCY_SIGNATURES.get(dep.name.lower())
        if hit:
            category, label = hit
            buckets[category].add(label)

    languages = sorted(
        (
            LanguageStat(
                name=name,
                files=lang_files[name],
                bytes=lang_bytes.get(name, 0),
                share=(lang_bytes.get(name, 0) / total_bytes) if total_bytes else 0.0,
            )
            for name in lang_files
        ),
        key=lambda s: (-s.bytes, s.name),
    )
    tech.languages = languages
    tech.primary_language = languages[0].name if languages else None
    tech.frameworks = sorted(buckets["frameworks"])
    tech.datastores = sorted(buckets["datastores"])
    tech.infrastructure = sorted(buckets["infrastructure"])
    tech.cloud = sorted(buckets["cloud"])
    tech.ml_ai = sorted(buckets["ml_ai"])
    tech.protocols = sorted(buckets["protocols"])
    tech.frontend = sorted(buckets["frontend"])
    tech.testing = sorted(buckets["testing"])
    tech.package_managers = sorted(buckets["package_managers"])
    tech.ci = sorted(buckets["ci"])
    tech.dependencies = sorted(dependencies.values(), key=lambda d: (d.ecosystem, d.name.lower()))
    tech.manifests = sorted(manifest_paths)
    tech.entrypoints = sorted(entrypoints)

    readme_text = _read_text(readme_path, MAX_README_BYTES) if readme_path else None
    description = _describe(readme_text, dependencies_root=root)
    excerpt = _excerpt(readme_text) if readme_text else None
    license_id = _detect_license(license_path, root)

    metrics = {
        "total_files": total_files if not truncated else max_files,
        "analyzed_files": analyzed_files,
        "total_bytes": total_bytes,
        "top_level_dirs": top_level_dirs[:30],
        "language_count": len(languages),
        "truncated": truncated,
    }
    return ScanResult(
        technology=tech,
        metrics=metrics,
        license=license_id,
        description=description,
        readme_excerpt=excerpt,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _join(rel_dir: str, name: str) -> str:
    return name if rel_dir == "." else f"{rel_dir}/{name}"


def _excluded(rel_path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(rel_path, pat.rstrip("/") + "/*")
        for pat in patterns
    )


def _match_file_signature(lower_name: str, rel_path: str, buckets: dict[str, set[str]]) -> None:
    for signature, (category, label) in FILE_SIGNATURES.items():
        if signature.startswith("*"):
            if lower_name.endswith(signature[1:]):
                buckets[category].add(label)
        elif "/" in signature:
            if rel_path.lower().endswith(signature):
                buckets[category].add(label)
        elif lower_name == signature:
            buckets[category].add(label)


def _read_text(path: Path | None, limit: int) -> str | None:
    if not path or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit)
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace")


def _excerpt(text: str) -> str:
    """A prose excerpt of a README: no badges, no images, no HTML."""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if stripped.startswith(("<", "![", "[!")) or "shields.io" in stripped:
            continue
        if re.fullmatch(r"[-=*_\s]{3,}", stripped):
            continue
        cleaned_lines.append(stripped)
    joined = "\n".join(cleaned_lines).strip()
    return joined[:READ_EXCERPT_CHARS]


def _describe(readme_text: str | None, *, dependencies_root: Path) -> str | None:
    """One-line description: manifest metadata first, then the README."""
    pyproject = dependencies_root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
            desc = (data.get("project") or {}).get("description")
            if desc:
                return str(desc).strip()
        except (tomllib.TOMLDecodeError, OSError):
            pass
    pkg = dependencies_root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            if data.get("description"):
                return str(data["description"]).strip()
        except (json.JSONDecodeError, OSError):
            pass
    if readme_text:
        for line in _excerpt(readme_text).splitlines():
            if line and not line.startswith("#"):
                return line[:400]
    return None


def _detect_license(license_path: Path | None, root: Path) -> str | None:
    text = _read_text(license_path, 20_000)
    if text:
        head = text[:4000].lower()
        for needle, spdx in SPDX_HINTS.items():
            if needle in head:
                return spdx
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
            project = data.get("project") or {}
            lic = project.get("license")
            if isinstance(lic, str):
                return lic
            if isinstance(lic, dict) and lic.get("text"):
                return str(lic["text"])
            classifiers = project.get("classifiers") or []
            for classifier in classifiers:
                if str(classifier).startswith("License ::"):
                    return str(classifier).split("::")[-1].strip()
        except (tomllib.TOMLDecodeError, OSError):
            pass
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            if data.get("license"):
                return str(data["license"])
        except (json.JSONDecodeError, OSError):
            pass
    if license_path is not None:
        return "UNKNOWN (license file present, text unrecognised)"
    return None


# ---------------------------------------------------------------------------
# Manifest parsers. Each returns a list of Dependency; all failures are soft --
# an unparseable manifest degrades detection, it never fails the run.
# ---------------------------------------------------------------------------
_REQ_LINE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._\-]*)\s*(?P<spec>[<>=!~\[].*)?$")
_PEP508_EXTRAS = re.compile(r"\[.*?\]")


def _parse_manifest(
    path: Path, filename: str, ecosystem: str, rel_path: str
) -> list[Dependency]:
    text = _read_text(path, MAX_MANIFEST_BYTES)
    if text is None:
        return []
    try:
        if filename.startswith("requirements") or filename == "Pipfile":
            return _parse_requirements(text, ecosystem, rel_path)
        if filename == "pyproject.toml":
            return _parse_pyproject(text, ecosystem, rel_path)
        if filename == "package.json":
            return _parse_package_json(text, ecosystem, rel_path)
        if filename == "go.mod":
            return _parse_go_mod(text, ecosystem, rel_path)
        if filename == "Cargo.toml":
            return _parse_cargo(text, ecosystem, rel_path)
        if filename == "composer.json":
            return _parse_composer(text, ecosystem, rel_path)
        if filename == "Gemfile":
            return _parse_gemfile(text, ecosystem, rel_path)
        if filename == "pom.xml":
            return _parse_pom(text, ecosystem, rel_path)
        if filename in {"build.gradle", "build.gradle.kts"}:
            return _parse_gradle(text, ecosystem, rel_path)
        if filename in {"environment.yml", "environment.yaml"}:
            return _parse_conda(text, ecosystem, rel_path)
    except Exception:
        return []
    return []


def _norm_py(name: str) -> str:
    return _PEP508_EXTRAS.sub("", name).strip().lower().replace("_", "-")


def _parse_requirements(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    deps: list[Dependency] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or line.startswith("["):
            continue
        if line.lower().startswith(("git+", "http://", "https://")):
            continue
        match = _REQ_LINE.match(line.split(";")[0].strip())
        if not match:
            continue
        name = _norm_py(match.group("name"))
        version = (match.group("spec") or "").strip() or None
        if version and version.startswith("["):
            version = None
        deps.append(Dependency(name=name, ecosystem=ecosystem, version=version, manifest=rel))
    return deps


def _parse_pyproject(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    data = tomllib.loads(text)
    deps: list[Dependency] = []
    project = data.get("project") or {}
    for entry in project.get("dependencies") or []:
        spec = str(entry).split(";")[0].strip()
        match = _REQ_LINE.match(spec)
        if match:
            deps.append(
                Dependency(
                    name=_norm_py(match.group("name")),
                    ecosystem=ecosystem,
                    version=(match.group("spec") or "").strip() or None,
                    manifest=rel,
                )
            )
    for group in (project.get("optional-dependencies") or {}).values():
        for entry in group:
            match = _REQ_LINE.match(str(entry).split(";")[0].strip())
            if match:
                deps.append(
                    Dependency(
                        name=_norm_py(match.group("name")), ecosystem=ecosystem, manifest=rel
                    )
                )
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        version = spec if isinstance(spec, str) else None
        deps.append(
            Dependency(name=_norm_py(name), ecosystem=ecosystem, version=version, manifest=rel)
        )
    return deps


def _parse_package_json(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    data = json.loads(text)
    deps: list[Dependency] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name, version in (data.get(section) or {}).items():
            deps.append(
                Dependency(
                    name=str(name).lower(),
                    ecosystem=ecosystem,
                    version=str(version),
                    manifest=rel,
                )
            )
    return deps


def _parse_go_mod(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    deps: list[Dependency] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        target = line[len("require ") :].strip() if line.startswith("require ") else (
            line if in_block else ""
        )
        if not target:
            continue
        parts = target.split()
        if parts:
            module = parts[0]
            short = module.rsplit("/", 1)[-1].lower()
            deps.append(
                Dependency(
                    name=short,
                    ecosystem=ecosystem,
                    version=parts[1] if len(parts) > 1 else None,
                    manifest=rel,
                )
            )
    return deps


def _parse_cargo(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    data = tomllib.loads(text)
    deps: list[Dependency] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, spec in (data.get(section) or {}).items():
            version = spec if isinstance(spec, str) else (spec or {}).get("version")
            deps.append(
                Dependency(
                    name=str(name).lower(), ecosystem=ecosystem, version=version, manifest=rel
                )
            )
    return deps


def _parse_composer(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    data = json.loads(text)
    deps: list[Dependency] = []
    for section in ("require", "require-dev"):
        for name, version in (data.get(section) or {}).items():
            deps.append(
                Dependency(
                    name=str(name).lower(),
                    ecosystem=ecosystem,
                    version=str(version),
                    manifest=rel,
                )
            )
    return deps


_GEM_RE = re.compile(r"""^\s*gem\s+['"](?P<name>[^'"]+)['"](?:\s*,\s*['"](?P<ver>[^'"]+)['"])?""")


def _parse_gemfile(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    deps = []
    for line in text.splitlines():
        match = _GEM_RE.match(line)
        if match:
            deps.append(
                Dependency(
                    name=match.group("name").lower(),
                    ecosystem=ecosystem,
                    version=match.group("ver"),
                    manifest=rel,
                )
            )
    return deps


_POM_DEP = re.compile(
    r"<dependency>.*?<groupId>(?P<group>[^<]+)</groupId>.*?"
    r"<artifactId>(?P<artifact>[^<]+)</artifactId>.*?</dependency>",
    re.DOTALL,
)


def _parse_pom(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    return [
        Dependency(
            name=match.group("artifact").strip().lower(), ecosystem=ecosystem, manifest=rel
        )
        for match in _POM_DEP.finditer(text)
    ]


_GRADLE_DEP = re.compile(
    r"""(?:implementation|api|compile|testImplementation|runtimeOnly)\s*[\('"]+"""
    r"""(?P<coord>[\w.\-]+:[\w.\-]+)"""
)


def _parse_gradle(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    deps = []
    for match in _GRADLE_DEP.finditer(text):
        coord = match.group("coord")
        deps.append(
            Dependency(name=coord.split(":")[-1].lower(), ecosystem=ecosystem, manifest=rel)
        )
    return deps


def _parse_conda(text: str, ecosystem: str, rel: str) -> list[Dependency]:
    deps: list[Dependency] = []
    in_deps = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("dependencies:"):
            in_deps = True
            continue
        if in_deps and line and not line.startswith((" ", "-", "\t")):
            break
        if in_deps and line.strip().startswith("- "):
            spec = line.strip()[2:].strip()
            if spec.endswith(":") or spec.startswith("pip"):
                continue
            name = re.split(r"[=<>! ]", spec, maxsplit=1)[0]
            if name:
                deps.append(
                    Dependency(name=_norm_py(name), ecosystem=ecosystem, manifest=rel)
                )
    return deps
