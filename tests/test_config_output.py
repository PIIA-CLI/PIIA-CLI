"""Configuration precedence and the dual-mode output contract."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from piia.config import Settings, redact
from piia.envelope import Envelope
from piia.errors import ConfigError, PiiaError
from piia.output.format import OutputFormat, resolve_format, supports_color
from piia.output.render import emit
from piia.output.schemas import DATA_SCHEMAS, all_schemas, envelope_schema


class TestConfigPrecedence:
    def test_defaults_when_nothing_is_set(self, tmp_path: Path) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        assert settings.llm.temperature == 0.1
        assert settings.llm.api_style == "openai"
        assert settings.analysis.clone_missing is True
        assert settings.document.formats == ["md", "json"]

    def test_environment_beats_dotenv_beats_config_file(self, tmp_path: Path) -> None:
        (tmp_path / "piia.yaml").write_text(
            "llm:\n  model: from-yaml\n  base_url: http://yaml\n", encoding="utf-8"
        )
        (tmp_path / ".env").write_text("LLM_MODEL_NAME=from-dotenv\n", encoding="utf-8")

        from_file = Settings.load(cwd=tmp_path, env={})
        assert from_file.llm.model == "from-dotenv"
        assert from_file.llm.base_url == "http://yaml"
        assert from_file.sources["LLM_MODEL_NAME"] == "dotenv"

        from_env = Settings.load(cwd=tmp_path, env={"LLM_MODEL_NAME": "from-env"})
        assert from_env.llm.model == "from-env"
        assert from_env.sources["LLM_MODEL_NAME"] == "environment"

    def test_cli_override_beats_everything(self, tmp_path: Path) -> None:
        settings = Settings.load(
            cwd=tmp_path,
            env={"LLM_MODEL_NAME": "from-env"},
            overrides={"llm.model": "from-flag"},
        )
        assert settings.llm.model == "from-flag"
        assert settings.sources["llm.model"] == "cli-flag"

    def test_search_roots_accept_a_comma_list(self, tmp_path: Path) -> None:
        settings = Settings.load(cwd=tmp_path, env={"PIIA_SEARCH_ROOTS": "/a,/b , /c"})
        assert settings.analysis.search_roots == ["/a", "/b", "/c"]

    def test_default_search_roots_include_the_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "project"
        nested.mkdir()
        settings = Settings.load(cwd=nested, env={})
        assert str(tmp_path.resolve()) in settings.analysis.search_roots

    def test_extra_headers_from_json_or_pairs(self, tmp_path: Path) -> None:
        as_json = Settings.load(cwd=tmp_path, env={"LLM_EXTRA_HEADERS": '{"X-A": "1"}'})
        assert as_json.llm.extra_headers == {"X-A": "1"}
        as_pairs = Settings.load(cwd=tmp_path, env={"LLM_EXTRA_HEADERS": "X-A=1,X-B=2"})
        assert as_pairs.llm.extra_headers == {"X-A": "1", "X-B": "2"}

    def test_bad_yaml_is_a_config_error_with_remediation(self, tmp_path: Path) -> None:
        (tmp_path / "piia.yaml").write_text("llm: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            Settings.load(cwd=tmp_path, env={})
        assert exc.value.remediation

    def test_require_names_every_missing_variable(self, tmp_path: Path) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        with pytest.raises(ConfigError) as exc:
            settings.llm.require()
        fields = {d["field"] for d in exc.value.details}
        assert fields == {"LLM_BASE_URL", "LLM_MODEL_NAME"}
        assert "--no-llm" in (exc.value.remediation or "")

    def test_unknown_api_style_is_rejected(self, tmp_path: Path) -> None:
        settings = Settings.load(
            cwd=tmp_path,
            env={"LLM_BASE_URL": "http://x", "LLM_MODEL_NAME": "m", "LLM_API_STYLE": "cohere"},
        )
        with pytest.raises(ConfigError):
            settings.llm.require()

    def test_describe_redacts_the_api_key(self, tmp_path: Path) -> None:
        settings = Settings.load(cwd=tmp_path, env={"LLM_API_KEY": "sk-secret-value-1234"})
        described = settings.describe()
        assert described["llm.api_key"]["value"] == "***1234"
        assert "secret" not in json.dumps(described)

    def test_redact_short_secrets_entirely(self) -> None:
        assert redact("abc") == "***"
        assert redact(None) == ""


class TestFormatResolution:
    def test_explicit_flag_wins(self) -> None:
        assert resolve_format("json-pretty") is OutputFormat.JSON_PRETTY
        assert resolve_format("ndjson") is OutputFormat.JSONL

    def test_json_shorthand(self) -> None:
        assert resolve_format(None, json_shorthand=True) is OutputFormat.JSON

    def test_env_var_is_consulted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIIA_OUTPUT_FORMAT", "jsonl")
        assert resolve_format(None, stream=_Tty(True)) is OutputFormat.JSONL

    def test_pipe_defaults_to_json_and_tty_to_human(self) -> None:
        assert resolve_format(None, stream=_Tty(False)) is OutputFormat.JSON
        assert resolve_format(None, stream=_Tty(True)) is OutputFormat.HUMAN

    def test_unknown_format_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown output format"):
            resolve_format("yaml")

    def test_json_never_gets_colour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert supports_color(OutputFormat.JSON, stream=_Tty(True)) is False

    def test_no_color_env_disables_colour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert supports_color(OutputFormat.HUMAN, stream=_Tty(True)) is False


class _Tty:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class TestEnvelope:
    def test_success_shape(self) -> None:
        env = Envelope.start("analyze").succeed({"a": 1})
        payload = env.to_dict()
        assert payload["status"] == "success"
        assert payload["command"] == "analyze"
        assert payload["data"] == {"a": 1}
        assert payload["error"] is None
        assert payload["execution"]["request_id"] == "req_test0000000000"
        assert payload["timestamp"] == "2026-01-01T00:00:00Z"

    def test_warnings_downgrade_status_to_partial(self) -> None:
        env = Envelope.start("analyze")
        env.warn("W", "something degraded")
        env.succeed({})
        assert env.to_dict()["status"] == "partial"

    def test_error_carries_code_and_exit_code(self) -> None:
        env = Envelope.start("generate").fail(
            PiiaError("boom", code="X_FAILED", exit_code=7, remediation="try this")
        )
        payload = env.to_dict()
        assert payload["status"] == "error"
        assert payload["data"] is None
        assert payload["error"]["code"] == "X_FAILED"
        assert payload["error"]["remediation"] == "try this"
        assert env.exit_code == 7


class TestRendering:
    def _emit(self, env: Envelope, fmt: OutputFormat) -> str:
        buffer = io.StringIO()
        emit(env, fmt, stream=buffer, color=False)
        return buffer.getvalue()

    def test_json_is_a_single_parseable_line(self) -> None:
        env = Envelope.start("analyze").succeed({"target": {"name": "app"}})
        out = self._emit(env, OutputFormat.JSON)
        assert out.count("\n") == 1
        assert json.loads(out)["data"]["target"]["name"] == "app"

    def test_json_contains_no_ansi(self) -> None:
        env = Envelope.start("doctor").succeed(
            {"checks": [{"name": "git", "status": "ok", "detail": "2.34"}], "summary": {}}
        )
        assert "\033[" not in self._emit(env, OutputFormat.JSON)

    def test_human_mode_renders_markdown_tables(self) -> None:
        env = Envelope.start("doctor").succeed(
            {
                "checks": [
                    {"name": "git", "status": "ok", "detail": "2.34.1", "remediation": None}
                ],
                "summary": {"passed": 1, "warnings": 0, "failures": 0},
            }
        )
        out = self._emit(env, OutputFormat.HUMAN)
        assert "## Environment check" in out
        assert "| Check | Status | Detail |" in out
        assert "✔ Ok" in out

    def test_human_error_shows_remediation_as_a_command_block(self) -> None:
        env = Envelope.start("generate").fail(
            PiiaError("no endpoint", code="CONFIG_INVALID", remediation="piia init")
        )
        out = self._emit(env, OutputFormat.HUMAN)
        assert "CONFIG_INVALID" in out
        assert "```bash\npiia init\n```" in out

    def test_jsonl_splits_lists_into_records(self) -> None:
        env = Envelope.start("analyze").succeed(
            {
                "overlaps": [{"repository": "a"}, {"repository": "b"}],
                "digest": "sha256:x",
            }
        )
        lines = [json.loads(ln) for ln in self._emit(env, OutputFormat.JSONL).splitlines()]
        assert lines[0]["record_type"] == "meta"
        assert [ln["repository"] for ln in lines if ln["record_type"] == "overlaps"] == ["a", "b"]
        assert lines[-1]["digest"] == "sha256:x"

    def test_unknown_command_falls_back_to_a_generic_view(self) -> None:
        env = Envelope.start("something-new").succeed({"count": 3, "items": ["a"]})
        out = self._emit(env, OutputFormat.HUMAN)
        assert "Count" in out and "3" in out


class TestSchemas:
    def test_every_command_has_a_schema(self) -> None:
        assert {"analyze", "generate", "doctor", "render", "version", "config show", "init"} <= set(
            DATA_SCHEMAS
        )

    def test_schema_specialises_the_data_property(self) -> None:
        schema = envelope_schema("analyze")
        assert schema["properties"]["data"]["title"] == "AnalyzeData"
        assert "target" in schema["properties"]["data"]["properties"]
        assert schema["properties"]["status"]["enum"] == ["success", "error", "partial"]

    def test_generic_schema_leaves_data_open(self) -> None:
        assert envelope_schema(None)["properties"]["data"] == {"type": ["object", "null"]}

    def test_all_schemas_is_one_document(self) -> None:
        every = all_schemas()
        assert every["commands"]["generate"]["properties"]["data"]["title"] == "GenerateData"
