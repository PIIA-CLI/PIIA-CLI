"""Configuration.

Precedence, highest first:

1. Command-line flags.
2. Process environment (``LLM_BASE_URL``, ``PIIA_SEARCH_ROOTS``, ...).
3. A ``.env`` file, searched upward from the working directory.
4. ``piia.yaml`` (or ``piia.local.yaml``, which wins over it), same search.
5. Built-in defaults.

Nothing here ever prints a secret: :meth:`Settings.describe` redacts API keys,
and the same redaction is what ``piia config show`` emits.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from piia.errors import ConfigError

CONFIG_FILENAMES = ("piia.local.yaml", "piia.local.yml", "piia.yaml", "piia.yml")
ENV_FILENAMES = (".env",)

DEFAULT_TEMPERATURE = 0.1
SECRET_KEYS = {"api_key"}


def _find_upward(names: tuple[str, ...], start: Path, limit: int = 6) -> Path | None:
    current = start.resolve()
    for _ in range(limit):
        for name in names:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Expected a number, got {value!r}.",
            details=[{"field": "value", "issue": str(exc)}],
            remediation="Check numeric settings such as LLM_TEMPERATURE and LLM_TIMEOUT.",
        ) from exc


def _as_int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Expected an integer, got {value!r}.") from exc


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    sep = "," if "," in str(value) else os.pathsep
    return [part.strip() for part in str(value).split(sep) if part.strip()]


@dataclass
class LLMSettings:
    """Everything needed to talk to any OpenAI- or Anthropic-shaped endpoint."""

    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int | None = 8192
    top_p: float | None = None
    seed: int | None = None
    timeout: float = 180.0
    max_retries: int = 3
    #: ``openai`` covers OpenAI, LiteLLM, vLLM, Ollama (/v1), OpenRouter,
    #: Together, Groq and Gemini's OpenAI-compatible endpoint. ``anthropic``
    #: uses the native Messages API.
    api_style: str = "openai"
    api_version: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    system_prompt: str | None = None
    verify_ssl: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def require(self) -> None:
        missing = [
            name
            for name, present in (("LLM_BASE_URL", self.base_url), ("LLM_MODEL_NAME", self.model))
            if not present
        ]
        if missing:
            raise ConfigError(
                "The model endpoint is not configured: " + ", ".join(missing) + " is unset.",
                details=[{"field": m, "issue": "required but not set"} for m in missing],
                remediation=(
                    "Run 'piia init' to write a .env template, then set LLM_BASE_URL and "
                    "LLM_MODEL_NAME (see docs/LLM_PROVIDERS.md). "
                    "Or pass --no-llm for a deterministic draft with no model call."
                ),
            )
        if self.api_style not in {"openai", "anthropic"}:
            raise ConfigError(
                f"Unsupported LLM_API_STYLE {self.api_style!r}.",
                remediation="Use 'openai' (default, works for most providers) or 'anthropic'.",
            )


@dataclass
class AnalysisSettings:
    """Deterministic-layer knobs."""

    #: Directories scanned for an existing local checkout before any clone.
    search_roots: list[str] = field(default_factory=list)
    search_depth: int = 2
    #: Clone repositories that were not found in ``search_roots``.
    clone_missing: bool = True
    workspace_dir: str = "workspace"
    use_repowise: bool = True
    repowise_bin: str = "repowise"
    repowise_timeout: float = 900.0
    #: Hard cap on files walked per repository, so a monorepo cannot hang a run.
    max_files: int = 60000
    exclude: list[str] = field(default_factory=list)
    git_history_limit: int = 2000


@dataclass
class DocumentSettings:
    """Facts about the agreement that the deterministic layer cannot infer."""

    company_name: str | None = None
    company_jurisdiction: str | None = None
    governing_law: str | None = None
    signatory_name: str | None = None
    signatory_title: str | None = None
    effective_date: str | None = None
    corporate_document: str | None = None
    output_dir: str = "piia-out"
    formats: list[str] = field(default_factory=lambda: ["md", "json"])


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    document: DocumentSettings = field(default_factory=DocumentSettings)
    #: Provenance: where each value came from, for ``piia config show``.
    sources: dict[str, str] = field(default_factory=dict)
    config_file: str | None = None
    env_file: str | None = None

    # -- loading -----------------------------------------------------------
    @classmethod
    def load(
        cls,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        config_path: Path | None = None,
        env_path: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Settings:
        """Merge every configuration source into one :class:`Settings`."""
        base = Path(cwd or Path.cwd())
        environ = dict(os.environ if env is None else env)

        env_file = env_path or _find_upward(ENV_FILENAMES, base)
        file_env: dict[str, str] = {}
        if env_file and env_file.is_file():
            file_env = {k: v for k, v in dotenv_values(env_file).items() if v is not None}

        cfg_file = config_path or _find_upward(CONFIG_FILENAMES, base)
        yaml_cfg: dict[str, Any] = {}
        if cfg_file and cfg_file.is_file():
            try:
                loaded = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"Could not parse {cfg_file}: {exc}",
                    remediation="Fix the YAML syntax, or delete the file to use defaults.",
                ) from exc
            if not isinstance(loaded, dict):
                raise ConfigError(f"{cfg_file} must contain a YAML mapping at the top level.")
            yaml_cfg = loaded

        settings = cls(config_file=str(cfg_file) if cfg_file else None,
                       env_file=str(env_file) if env_file else None)

        def pick(env_key: str, *yaml_path: str, default: Any = None) -> tuple[Any, str]:
            """Return ``(value, source)`` honouring the documented precedence."""
            if env_key in environ and environ[env_key] != "":
                return environ[env_key], "environment"
            if env_key in file_env and file_env[env_key] != "":
                return file_env[env_key], "dotenv"
            node: Any = yaml_cfg
            for part in yaml_path:
                if not isinstance(node, dict) or part not in node:
                    node = None
                    break
                node = node[part]
            if node not in (None, ""):
                return node, "config-file"
            return default, "default"

        def set_(target: Any, attr: str, env_key: str, *yaml_path: str,
                 cast: Any = None, default: Any = None) -> None:
            raw, source = pick(env_key, *yaml_path, default=default)
            value = cast(raw) if (cast and raw is not None) else raw
            if value is not None:
                setattr(target, attr, value)
            settings.sources[env_key] = source

        llm = settings.llm
        set_(llm, "base_url", "LLM_BASE_URL", "llm", "base_url", cast=str)
        set_(llm, "api_key", "LLM_API_KEY", "llm", "api_key", cast=str)
        set_(llm, "model", "LLM_MODEL_NAME", "llm", "model", cast=str)
        set_(llm, "temperature", "LLM_TEMPERATURE", "llm", "temperature",
             cast=lambda v: _as_float(v, DEFAULT_TEMPERATURE))
        set_(llm, "max_tokens", "LLM_MAX_TOKENS", "llm", "max_tokens",
             cast=lambda v: _as_int(v, 8192))
        set_(llm, "top_p", "LLM_TOP_P", "llm", "top_p", cast=lambda v: _as_float(v, 1.0))
        set_(llm, "seed", "LLM_SEED", "llm", "seed", cast=lambda v: _as_int(v, None))
        set_(llm, "timeout", "LLM_TIMEOUT", "llm", "timeout",
             cast=lambda v: _as_float(v, 180.0))
        set_(llm, "max_retries", "LLM_MAX_RETRIES", "llm", "max_retries",
             cast=lambda v: _as_int(v, 3))
        set_(llm, "api_style", "LLM_API_STYLE", "llm", "api_style",
             cast=lambda v: str(v).strip().lower())
        set_(llm, "api_version", "LLM_API_VERSION", "llm", "api_version", cast=str)
        set_(llm, "verify_ssl", "LLM_VERIFY_SSL", "llm", "verify_ssl",
             cast=lambda v: _as_bool(v, True))
        set_(llm, "extra_headers", "LLM_EXTRA_HEADERS", "llm", "extra_headers",
             cast=_parse_headers)

        prompt_raw, prompt_source = pick("LLM_SYSTEM_PROMPT", "llm", "system_prompt")
        prompt_file, prompt_file_source = pick(
            "LLM_SYSTEM_PROMPT_FILE", "llm", "system_prompt_file"
        )
        if prompt_file:
            path = Path(str(prompt_file)).expanduser()
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                raise ConfigError(
                    f"LLM_SYSTEM_PROMPT_FILE points at a missing file: {path}",
                    remediation="Create the file or unset LLM_SYSTEM_PROMPT_FILE.",
                )
            llm.system_prompt = path.read_text(encoding="utf-8")
            settings.sources["LLM_SYSTEM_PROMPT"] = f"{prompt_file_source} (file)"
        elif prompt_raw:
            llm.system_prompt = str(prompt_raw)
            settings.sources["LLM_SYSTEM_PROMPT"] = prompt_source

        an = settings.analysis
        roots, roots_source = pick("PIIA_SEARCH_ROOTS", "analysis", "search_roots")
        an.search_roots = _as_list(roots) or _default_search_roots(base)
        settings.sources["PIIA_SEARCH_ROOTS"] = (
            roots_source if roots else "default (parent of working directory)"
        )
        set_(an, "search_depth", "PIIA_SEARCH_DEPTH", "analysis", "search_depth",
             cast=lambda v: _as_int(v, 2))
        set_(an, "clone_missing", "PIIA_CLONE_MISSING", "analysis", "clone_missing",
             cast=lambda v: _as_bool(v, True))
        set_(an, "workspace_dir", "PIIA_WORKSPACE_DIR", "analysis", "workspace_dir", cast=str)
        set_(an, "use_repowise", "PIIA_USE_REPOWISE", "analysis", "use_repowise",
             cast=lambda v: _as_bool(v, True))
        set_(an, "repowise_bin", "PIIA_REPOWISE_BIN", "analysis", "repowise_bin", cast=str)
        set_(an, "repowise_timeout", "PIIA_REPOWISE_TIMEOUT", "analysis", "repowise_timeout",
             cast=lambda v: _as_float(v, 900.0))
        set_(an, "max_files", "PIIA_MAX_FILES", "analysis", "max_files",
             cast=lambda v: _as_int(v, 60000))
        excl, excl_source = pick("PIIA_EXCLUDE", "analysis", "exclude")
        an.exclude = _as_list(excl)
        settings.sources["PIIA_EXCLUDE"] = excl_source

        doc = settings.document
        set_(doc, "company_name", "PIIA_COMPANY_NAME", "document", "company_name", cast=str)
        set_(doc, "company_jurisdiction", "PIIA_COMPANY_JURISDICTION", "document",
             "company_jurisdiction", cast=str)
        set_(doc, "governing_law", "PIIA_GOVERNING_LAW", "document", "governing_law", cast=str)
        set_(doc, "signatory_name", "PIIA_SIGNATORY_NAME", "document", "signatory_name", cast=str)
        set_(doc, "signatory_title", "PIIA_SIGNATORY_TITLE", "document", "signatory_title",
             cast=str)
        set_(doc, "effective_date", "PIIA_EFFECTIVE_DATE", "document", "effective_date", cast=str)
        set_(doc, "corporate_document", "PIIA_CORPORATE_DOCUMENT", "document",
             "corporate_document", cast=str)
        set_(doc, "output_dir", "PIIA_OUTPUT_DIR", "document", "output_dir", cast=str)
        fmts, fmts_source = pick("PIIA_DOCUMENT_FORMATS", "document", "formats")
        if fmts:
            doc.formats = [f.lower() for f in _as_list(fmts)]
        settings.sources["PIIA_DOCUMENT_FORMATS"] = fmts_source

        for dotted, value in (overrides or {}).items():
            if value is None:
                continue
            settings.apply_override(dotted, value)

        return settings

    def apply_override(self, dotted: str, value: Any) -> None:
        """Apply one ``section.attr`` CLI override and record its provenance."""
        section_name, _, attr = dotted.partition(".")
        section = getattr(self, section_name, None)
        if section is None or not hasattr(section, attr):
            raise ConfigError(f"Unknown setting {dotted!r}.")
        current = getattr(section, attr)
        if isinstance(current, list) and not isinstance(value, list):
            value = _as_list(value)
        elif isinstance(current, bool):
            value = _as_bool(value, current)
        elif isinstance(current, float) and not isinstance(value, bool):
            value = _as_float(value, current)
        setattr(section, attr, value)
        self.sources[dotted] = "cli-flag"

    # -- introspection -----------------------------------------------------
    def describe(self) -> dict[str, dict[str, Any]]:
        """Redacted, source-annotated view of every setting."""
        out: dict[str, dict[str, Any]] = {}
        env_names = {
            "llm": {
                "base_url": "LLM_BASE_URL",
                "api_key": "LLM_API_KEY",
                "model": "LLM_MODEL_NAME",
                "temperature": "LLM_TEMPERATURE",
                "max_tokens": "LLM_MAX_TOKENS",
                "top_p": "LLM_TOP_P",
                "seed": "LLM_SEED",
                "timeout": "LLM_TIMEOUT",
                "max_retries": "LLM_MAX_RETRIES",
                "api_style": "LLM_API_STYLE",
                "api_version": "LLM_API_VERSION",
                "extra_headers": "LLM_EXTRA_HEADERS",
                "system_prompt": "LLM_SYSTEM_PROMPT",
                "verify_ssl": "LLM_VERIFY_SSL",
            },
            "analysis": {
                "search_roots": "PIIA_SEARCH_ROOTS",
                "search_depth": "PIIA_SEARCH_DEPTH",
                "clone_missing": "PIIA_CLONE_MISSING",
                "workspace_dir": "PIIA_WORKSPACE_DIR",
                "use_repowise": "PIIA_USE_REPOWISE",
                "repowise_bin": "PIIA_REPOWISE_BIN",
                "repowise_timeout": "PIIA_REPOWISE_TIMEOUT",
                "max_files": "PIIA_MAX_FILES",
                "exclude": "PIIA_EXCLUDE",
                "git_history_limit": "PIIA_GIT_HISTORY_LIMIT",
            },
            "document": {
                "company_name": "PIIA_COMPANY_NAME",
                "company_jurisdiction": "PIIA_COMPANY_JURISDICTION",
                "governing_law": "PIIA_GOVERNING_LAW",
                "signatory_name": "PIIA_SIGNATORY_NAME",
                "signatory_title": "PIIA_SIGNATORY_TITLE",
                "effective_date": "PIIA_EFFECTIVE_DATE",
                "corporate_document": "PIIA_CORPORATE_DOCUMENT",
                "output_dir": "PIIA_OUTPUT_DIR",
                "formats": "PIIA_DOCUMENT_FORMATS",
            },
        }
        for section_name, mapping in env_names.items():
            section = getattr(self, section_name)
            for attr, env_key in mapping.items():
                if not any(f.name == attr for f in fields(section)):
                    continue
                value = getattr(section, attr)
                if attr in SECRET_KEYS:
                    value = redact(value)
                elif attr == "system_prompt" and value:
                    value = f"<{len(str(value))} chars>"
                elif isinstance(value, list):
                    value = ", ".join(str(v) for v in value) or "-"
                elif isinstance(value, dict):
                    value = ", ".join(sorted(value)) or "-"
                out[f"{section_name}.{attr}"] = {
                    "value": "" if value is None else value,
                    "source": self.sources.get(f"{section_name}.{attr}")
                    or self.sources.get(env_key, "default"),
                    "env_var": env_key,
                }
        return out


def redact(value: str | None) -> str:
    """Show only that a secret exists, plus its last four characters."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"***{value[-4:]}"


def _parse_headers(raw: Any) -> dict[str, str]:
    """Accept a JSON object or a ``K=V,K=V`` string for extra HTTP headers."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    text = str(raw).strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"LLM_EXTRA_HEADERS is not valid JSON: {exc}",
                remediation='Use JSON, e.g. LLM_EXTRA_HEADERS=\'{"X-Org": "acme"}\'.',
            ) from exc
        return {str(k): str(v) for k, v in parsed.items()}
    headers: dict[str, str] = {}
    for pair in text.split(","):
        if "=" in pair:
            key, _, value = pair.partition("=")
            headers[key.strip()] = value.strip()
    return headers


def _default_search_roots(base: Path) -> list[str]:
    """Default to the working directory and its parent.

    Sibling-checkout layouts (``~/src/<repo>/``) are the common case, so the
    parent directory is where an already-cloned prior work usually lives.
    """
    roots = [str(base.resolve())]
    parent = base.resolve().parent
    if parent != base.resolve():
        roots.append(str(parent))
    return roots
