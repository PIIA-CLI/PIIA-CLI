"""Compatibility projections for the optional Repowise subprocess adapter."""

from __future__ import annotations

from piia.analysis.repowise import (
    _first_json_value,
    _parse_structurizr,
    _trim_dead_code,
)


def test_json_extractor_accepts_dead_code_array_after_status_text() -> None:
    payload = _first_json_value('repowise dead-code — /repo\n[{"kind":"unused_export"}]\n')
    assert payload == [{"kind": "unused_export"}]


def test_structurizr_projection_extracts_bounded_architecture_facts() -> None:
    dsl = """
workspace "Example" {
  model {
    pkg = container "api" "12 files" "python" {
      properties { "repowise.layers" "Application, Config" }
      cmp_src = component "src" "10 files" {
        properties { "repowise.layers" "Application" }
      }
    }
    cmp_src -> pkg "imports"
  }
}
"""
    result = _parse_structurizr(dsl)
    assert result["workspace"] == "Example"
    assert result["layers"] == ["Application", "Config"]
    assert result["containers"][0]["technology"] == "python"
    assert result["components"][0]["name"] == "src"
    assert result["relationships"][0]["kind"] == "imports"


def test_dead_code_projection_normalizes_current_repowise_list_shape() -> None:
    result = _trim_dead_code(
        [
            {
                "kind": "unused_export",
                "file_path": "src/example.py",
                "symbol_name": "unused",
                "confidence": 1.0,
            }
        ]
    )
    assert result["total"] == 1
    assert result["counts"] == {"unused_export": 1}
    assert result["findings"][0]["path"] == "src/example.py"
    assert result["findings"][0]["symbol"] == "unused"
