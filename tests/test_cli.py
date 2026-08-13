"""The CLI contract: format resolution, exit codes, and --schema everywhere.

These tests exist because the contract is the product for an agent. A command
that cannot report its own schema, or that returns Click's plain-text error
instead of an envelope, is broken even if the analysis underneath is perfect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from piia.cli import cli
from piia.output.schemas import DATA_SCHEMAS


@pytest.fixture
def run() -> CliRunner:
    return CliRunner()


def envelope(result: object) -> dict:
    """Parse the single JSON document a command emits on stdout.

    Deliberately reads ``stdout`` rather than the combined ``output``: the
    contract is that JSON goes to stdout and progress goes to stderr, so a test
    that parsed the combination would not be testing it.
    """
    return json.loads(result.stdout)  # type: ignore[attr-defined]


class TestSchemaDiscovery:
    @pytest.mark.parametrize("command", ["analyze", "generate", "render", "doctor", "version"])
    def test_schema_works_without_a_valid_invocation(self, run: CliRunner, command: str) -> None:
        """--schema must not require the command's own arguments.

        This is the whole point of the flag: an agent asks what a command emits
        *before* it knows how to call it.
        """
        result = run.invoke(cli, [command, "--schema"])
        assert result.exit_code == 0, result.output
        schema = json.loads(result.output)
        assert schema["properties"]["data"]["title"] == DATA_SCHEMAS[command]["title"]
        assert schema["$schema"].startswith("https://json-schema.org/draft/2020-12")

    def test_schema_subcommand_forms(self, run: CliRunner) -> None:
        assert run.invoke(cli, ["schema"]).exit_code == 0
        by_name = run.invoke(cli, ["schema", "analyze"])
        assert json.loads(by_name.stdout)["properties"]["data"]["title"] == "AnalyzeData"
        every = json.loads(run.invoke(cli, ["schema", "--all"]).stdout)
        assert set(every["commands"]) == set(DATA_SCHEMAS)

    def test_unknown_command_name_is_rejected_with_the_valid_list(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["schema", "nonsense"])
        assert result.exit_code != 0
        assert "analyze" in result.output


class TestUsageErrors:
    def test_missing_target_is_a_structured_envelope_not_click_text(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["analyze", "--prior", "x", "--json"])
        assert result.exit_code == 2
        payload = envelope(result)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "USAGE_INVALID"
        assert payload["error"]["details"][0]["field"] == "target"
        assert "--target" in payload["error"]["remediation"]

    def test_missing_priors_explains_both_ways_to_supply_them(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["analyze", "-t", ".", "--json"])
        assert result.exit_code == 2
        error = envelope(result)["error"]
        assert error["code"] == "USAGE_INVALID"
        assert "--priors-file" in error["remediation"]

    def test_render_without_a_document(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["render", "--json"])
        assert result.exit_code == 2
        assert "piia generate" in envelope(result)["error"]["remediation"]

    def test_generate_without_a_model_endpoint_says_how_to_proceed(
        self, run: CliRunner, tmp_path: Path
    ) -> None:
        priors = tmp_path / "priors.txt"
        priors.write_text("acme/thing\n", encoding="utf-8")
        # An explicit empty --env-file, so the upward search cannot find a real
        # .env belonging to whoever is running the suite.
        empty_env = tmp_path / "empty.env"
        empty_env.write_text("", encoding="utf-8")
        result = run.invoke(
            cli,
            [
                "generate",
                "-t", str(tmp_path),
                "--priors-file", str(priors),
                "--signatory", "A",
                "--env-file", str(empty_env),
                "--json",
            ],
        )
        assert result.exit_code == 2
        error = envelope(result)["error"]
        assert error["code"] == "CONFIG_INVALID"
        assert "--no-llm" in error["remediation"]

    def test_bad_output_format_is_rejected_by_click(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["version", "-o", "yaml"])
        assert result.exit_code != 0


class TestOutputModes:
    def test_json_stdout_is_exactly_one_document(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["version", "--json"])
        assert result.exit_code == 0
        payload = envelope(result)
        assert payload["command"] == "version"
        assert payload["status"] == "success"
        assert payload["data"]["license"] == "AGPL-3.0-or-later"

    def test_human_mode_is_markdown_not_json(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["version", "-o", "human"])
        assert result.exit_code == 0
        assert result.stdout.startswith("piia ")
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stdout)

    def test_json_pretty_is_indented(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["version", "-o", "json-pretty"])
        assert '\n  "version"' in result.stdout

    def test_no_ansi_escapes_in_json_mode(self, run: CliRunner) -> None:
        assert "\033[" not in run.invoke(cli, ["version", "--json"]).stdout

    def test_env_var_selects_the_format(
        self, run: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PIIA_OUTPUT_FORMAT", "json")
        assert envelope(run.invoke(cli, ["version"]))["status"] == "success"


class TestDoctor:
    def test_skip_llm_reports_no_failures_in_a_clean_environment(self, run: CliRunner) -> None:
        result = run.invoke(cli, ["doctor", "--skip-llm", "--json"])
        assert result.exit_code == 0, result.output
        data = envelope(result)["data"]
        assert data["summary"]["failures"] == 0
        names = {c["name"] for c in data["checks"]}
        assert {"piia", "python", "git", "repowise", "model endpoint"} <= names
        endpoint = next(c for c in data["checks"] if c["name"] == "model endpoint")
        assert endpoint["status"] == "skipped"

    def test_every_non_ok_check_carries_a_remediation(self, run: CliRunner) -> None:
        data = envelope(run.invoke(cli, ["doctor", "--skip-llm", "--json"]))["data"]
        for check in data["checks"]:
            if check["status"] in {"warn", "error"}:
                assert check["remediation"], check


class TestInitAndConfig:
    def test_init_writes_templates_and_protects_the_env_file(
        self, run: CliRunner, tmp_path: Path
    ) -> None:
        result = run.invoke(cli, ["init", "-d", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        written = envelope(result)["data"]["written"]
        assert any(name.endswith(".env") for name in written)
        assert (tmp_path / "piia.yaml").is_file()
        assert (tmp_path / "priors.txt").is_file()
        assert ".env" in (tmp_path / ".gitignore").read_text()
        assert "LLM_BASE_URL" in (tmp_path / ".env").read_text()

    def test_init_is_idempotent_without_force(self, run: CliRunner, tmp_path: Path) -> None:
        run.invoke(cli, ["init", "-d", str(tmp_path), "--json"])
        (tmp_path / ".env").write_text("LLM_MODEL_NAME=mine\n", encoding="utf-8")
        again = envelope(run.invoke(cli, ["init", "-d", str(tmp_path), "--json"]))
        assert any(p.endswith(".env") for p in again["data"]["skipped"])
        assert (tmp_path / ".env").read_text() == "LLM_MODEL_NAME=mine\n"

    def test_config_show_redacts_secrets(
        self, run: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-tail")
        result = run.invoke(cli, ["config", "show", "--json"])
        assert result.exit_code == 0
        assert "super-secret" not in result.stdout
        settings = envelope(result)["data"]["settings"]
        assert settings["llm.api_key"]["value"] == "***tail"
        assert settings["llm.api_key"]["source"] == "environment"


class TestEndToEndThroughTheCli:
    def test_analyze_then_generate_then_render_with_no_model(
        self,
        run: CliRunner,
        tmp_path: Path,
        target_repo: Path,
        related_prior_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The full pipeline must work with no model and no network."""
        monkeypatch.setenv("PIIA_USE_REPOWISE", "false")
        common = [
            "-t",
            str(target_repo),
            "-p",
            str(related_prior_repo),
            "--no-clone-missing",
            "--search-root",
            str(target_repo.parent),
        ]

        analysis = run.invoke(cli, ["analyze", *common, "--json"])
        assert analysis.exit_code == 0, analysis.output
        data = envelope(analysis)["data"]
        assert data["target"]["name"] == "company-app"
        assert data["overlaps"][0]["repository"] == "earlier-vision"
        assert data["digest"].startswith("sha256:")

        out = tmp_path / "out"
        generated = run.invoke(
            cli,
            [
                "generate", *common, "--no-llm",
                "--signatory", "Ada Lovelace",
                "--company", "Acme, Inc.",
                "--governing-law", "the State of Delaware",
                "-d", str(out), "-f", "md", "-f", "json", "-f", "html",
                "--json",
            ],
        )
        assert generated.exit_code == 0, generated.output
        payload = envelope(generated)["data"]
        assert payload["document"]["prior_inventions"][0]["source"] == "deterministic"
        assert payload["provenance"]["model"] is None
        assert payload["analysis_digest"] == data["digest"]
        assert (out / "piia.md").is_file()
        assert (out / "piia.html").is_file()
        assert (out / "analysis.json").is_file()

        rendered = run.invoke(
            cli, ["render", str(out / "piia.json"), "-f", "md", "-d", str(tmp_path / "again"),
                  "--json"]
        )
        assert rendered.exit_code == 0, rendered.output
        assert (tmp_path / "again" / "piia.md").is_file()

    def test_dry_run_reports_the_plan_without_writing_anything(
        self,
        run: CliRunner,
        tmp_path: Path,
        target_repo: Path,
        related_prior_repo: Path,
    ) -> None:
        out = tmp_path / "never"
        result = run.invoke(
            cli,
            [
                "generate",
                "-t", str(target_repo),
                "-p", str(related_prior_repo),
                "--signatory", "Ada Lovelace",
                "--company", "Acme, Inc.",
                "--no-llm", "--dry-run",
                "-d", str(out),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        plan = envelope(result)["data"]
        assert plan["dry_run"] is True
        assert plan["model"]["enabled"] is False
        assert plan["model"]["estimated_calls"] == 0
        assert not out.exists()

    def test_progress_goes_to_stderr_leaving_stdout_parseable(
        self, tmp_path: Path, target_repo: Path, related_prior_repo: Path
    ) -> None:
        """The stream split is what lets an agent pipe stdout into a parser."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                "-t", str(target_repo),
                "-p", str(related_prior_repo),
                "--no-clone-missing",
                "--search-root", str(target_repo.parent),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stderr
        envelope(result)  # parses cleanly: no progress lines mixed in
        assert "Scanning" in result.stderr

    def test_quiet_silences_progress(
        self, target_repo: Path, related_prior_repo: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze", "-t", str(target_repo), "-p", str(related_prior_repo),
                "--no-clone-missing", "--search-root", str(target_repo.parent), "-q", "--json",
            ],
        )
        assert result.exit_code == 0
        assert result.stderr == ""
