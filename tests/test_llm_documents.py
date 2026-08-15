"""The model client, drafting fallbacks, corporate parsing and rendering."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from piia.config import LLMSettings, Settings
from piia.documents.assemble import assemble
from piia.documents.corporate import parse_corporate_document
from piia.documents.writer import (
    render_exhibit_a,
    render_html,
    render_markdown,
    render_pdf,
    write_documents,
)
from piia.errors import DocumentError, LLMError, LLMResponseError, UsageError
from piia.llm.client import ChatClient, Message, extract_json
from piia.llm.drafting import _technology_key, draft
from piia.llm.prompts import compact_repo, prior_inventions_messages


def _settings(**kwargs: object) -> LLMSettings:
    base = {"base_url": "http://model.local/v1", "model": "test-model", "max_retries": 2}
    base.update(kwargs)
    return LLMSettings(**base)  # type: ignore[arg-type]


def test_technology_key_accepts_canonical_parenthetical_display_names() -> None:
    assert _technology_key("Chroma") == _technology_key("Chroma (vector DB)")


class TestExtractJson:
    def test_bare_object(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_block(self) -> None:
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_object_wrapped_in_prose(self) -> None:
        assert extract_json('Sure! Here you go:\n{"a": {"b": 2}}\nHope that helps.') == {
            "a": {"b": 2}
        }

    def test_braces_inside_strings_do_not_confuse_it(self) -> None:
        assert extract_json('{"text": "a { brace } inside"}')["text"] == "a { brace } inside"

    def test_returns_none_for_prose(self) -> None:
        assert extract_json("I cannot do that.") is None


class TestEndpointShapes:
    def test_openai_endpoint_and_headers(self) -> None:
        client = ChatClient(_settings(api_key="sk-x"))
        assert client.endpoint == "http://model.local/v1/chat/completions"
        assert client.headers()["Authorization"] == "Bearer sk-x"

    def test_endpoint_already_complete_is_left_alone(self) -> None:
        client = ChatClient(_settings(base_url="http://x/v1/chat/completions"))
        assert client.endpoint == "http://x/v1/chat/completions"

    def test_anthropic_endpoint_and_headers(self) -> None:
        client = ChatClient(
            _settings(base_url="https://api.anthropic.com", api_style="anthropic", api_key="k")
        )
        assert client.endpoint == "https://api.anthropic.com/v1/messages"
        headers = client.headers()
        assert headers["x-api-key"] == "k"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers

    def test_extra_headers_are_merged(self) -> None:
        client = ChatClient(_settings(extra_headers={"X-Org": "acme"}))
        assert client.headers()["X-Org"] == "acme"

    def test_anthropic_body_lifts_system_out_of_messages(self) -> None:
        client = ChatClient(_settings(api_style="anthropic"))
        body = client._body([Message("system", "be brief"), Message("user", "hi")], json_mode=False)
        assert body["system"] == "be brief"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["max_tokens"]


class TestChatClient:
    def test_openai_reply_is_parsed(self, openai_reply) -> None:
        client = ChatClient(_settings(), transport=openai_reply(["hello"]))
        response = client.complete([Message("user", "hi")])
        assert response.text == "hello"
        assert response.usage["total_tokens"] == 10
        assert client.total_usage["total_tokens"] == 10

    def test_anthropic_reply_is_parsed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "claude",
                    "content": [{"type": "text", "text": "hi there"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
                request=request,
            )

        client = ChatClient(
            _settings(api_style="anthropic"), transport=httpx.MockTransport(handler)
        )
        response = client.complete([Message("user", "hi")])
        assert response.text == "hi there"
        assert response.finish_reason == "end_turn"

    def test_response_format_rejection_falls_back_to_prompt_json(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            if "response_format" in body:
                return httpx.Response(
                    400, json={"error": "response_format is not supported"}, request=request
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ok": true}'}}]},
                request=request,
            )

        client = ChatClient(_settings(), transport=httpx.MockTransport(handler))
        data, _ = client.complete_json([Message("user", "hi")], required_keys=("ok",))
        assert data == {"ok": True}
        assert "response_format" in seen[0] and "response_format" not in seen[1]

    def test_server_error_is_retried_then_reported(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, text="unavailable", request=request)

        client = ChatClient(_settings(max_retries=2), transport=httpx.MockTransport(handler))
        with pytest.raises(LLMError) as exc:
            client.complete([Message("user", "hi")])
        assert calls["n"] == 2
        assert "--no-llm" in (exc.value.remediation or "")

    def test_auth_failure_is_not_retried_and_explains_itself(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, text="bad key", request=request)

        client = ChatClient(_settings(), transport=httpx.MockTransport(handler))
        with pytest.raises(LLMError) as exc:
            client.complete([Message("user", "hi")])
        assert calls["n"] == 1
        assert "LLM_API_KEY" in (exc.value.remediation or "")

    def test_json_repair_loop_succeeds_on_second_try(self, openai_reply) -> None:
        client = ChatClient(
            _settings(), transport=openai_reply(["not json at all", '{"prior_inventions": []}'])
        )
        data, _ = client.complete_json([Message("user", "hi")], required_keys=("prior_inventions",))
        assert data == {"prior_inventions": []}

    def test_json_repair_gives_up_with_guidance(self, openai_reply) -> None:
        client = ChatClient(_settings(), transport=openai_reply(["nope"]))
        with pytest.raises(LLMResponseError) as exc:
            client.complete_json([Message("user", "hi")], required_keys=("x",), max_repairs=1)
        assert "--no-llm" in (exc.value.remediation or "")


class TestPrompts:
    def test_compact_repo_carries_evidence_not_prose(self, bundle) -> None:
        prior = bundle.prior_works[0]
        payload = compact_repo(prior, bundle.overlap_for(prior.name))
        assert payload["repository"] == "earlier-vision"
        assert payload["comparison_to_target"]["relatedness"] == "high"
        assert "PyTorch" in payload["ai_ml"]

    def test_prompt_names_every_repository_in_the_batch(self, bundle) -> None:
        messages = prior_inventions_messages(
            bundle,
            bundle.prior_works,
            company_name="Acme",
            signatory_name="Ada Lovelace",
            company_business="Acme builds things.",
        )
        user = messages[-1].content
        assert "earlier-vision" in user and "rust-tool" in user
        assert "Ada Lovelace" in user
        assert "one JSON object" in user

    def test_system_prompt_can_be_overridden(self, bundle) -> None:
        messages = prior_inventions_messages(
            bundle,
            bundle.prior_works[:1],
            company_name="Acme",
            signatory_name="Ada",
            company_business="x",
            system_override="CUSTOM SYSTEM",
        )
        assert messages[0].content == "CUSTOM SYSTEM"


class TestDraftingFallbacks:
    def test_no_client_produces_a_complete_deterministic_draft(self, bundle) -> None:
        content = draft(bundle, company_name="Acme, Inc.", signatory_name="Ada Lovelace")
        assert content.used_model is False
        assert len(content.prior_inventions) == 2
        assert all(pi["source"] == "deterministic" for pi in content.prior_inventions)
        assert all(pi["carve_out_language"] for pi in content.prior_inventions)
        assert content.company_business and content.executive_summary
        assert content.review_checklist

    def test_deterministic_checklist_flags_copyleft_and_joint_authorship(self, bundle) -> None:
        content = draft(bundle, company_name="Acme", signatory_name="Ada")
        items = " ".join(i["item"] + i["why"] for i in content.review_checklist)
        assert "AGPL-3.0" in items
        assert "authorship" in items
        assert "license" in items.lower()

    def test_high_relatedness_marks_incorporation_risk(self, bundle) -> None:
        content = draft(bundle, company_name="Acme", signatory_name="Ada")
        by_repo = {pi["repository"]: pi for pi in content.prior_inventions}
        assert by_repo["earlier-vision"]["incorporation_risk"] == "likely"
        assert by_repo["rust-tool"]["incorporation_risk"] == "none"

    def test_model_prose_is_merged_but_facts_stay_deterministic(self, bundle, openai_reply) -> None:
        replies = [
            json.dumps(
                {
                    "company_business": "Acme builds inference services.",
                    "technical_field": "Applied ML.",
                    "executive_summary": "Summary from the model.",
                }
            ),
            json.dumps(
                {
                    "prior_inventions": [
                        {
                            "repository": "earlier-vision",
                            "title": "Vision Research Toolkit",
                            "description": "Model-written description.",
                            "technologies": ["PyTorch", "Quantum Blockchain"],
                            "relation_to_company_business": "Closely related.",
                            "carve_out_language": "Ada retains this work.",
                            "incorporation_risk": "likely",
                            "notes": "",
                        },
                        {
                            "repository": "rust-tool",
                            "title": "Rust File Tool",
                            "description": "Another description.",
                            "technologies": [],
                            "relation_to_company_business": "Unrelated.",
                            "carve_out_language": "Ada retains this too.",
                            "incorporation_risk": "none",
                            "notes": "",
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "review_checklist": [
                        {"item": "Model item", "why": "Model reason", "severity": "high"}
                    ],
                    "open_source_notes": ["A model note."],
                    "supplemental_clauses": [
                        {"heading": "Copyleft", "text": "Clause text.", "rationale": "AGPL."}
                    ],
                }
            ),
        ]
        client = ChatClient(_settings(), transport=openai_reply(replies))
        content = draft(
            bundle,
            company_name="Acme, Inc.",
            signatory_name="Ada Lovelace",
            client=client,
            batch_size=10,
        )
        assert content.used_model is True
        assert content.company_business == "Acme builds inference services."
        entry = next(pi for pi in content.prior_inventions if pi["repository"] == "earlier-vision")
        assert entry["title"] == "Vision Research Toolkit"
        assert entry["source"] == "model"
        # Deterministic facts survive the merge...
        assert entry["license"] == "AGPL-3.0"
        assert entry["commit"] == "a" * 40
        assert entry["relatedness"] == "high"
        # ...and an unsupported technology is quarantined, not silently accepted.
        assert "Quantum Blockchain" in entry["unverified_technologies"]
        # ...while a technology the scan did find is NOT quarantined, even though
        # it is absent from the shortened display list.
        assert "PyTorch" in entry["technologies"]
        assert "PyTorch" not in entry["unverified_technologies"]
        assert "OpenCV" not in entry.get("unverified_technologies", [])
        # Deterministic checklist items are kept alongside the model's.
        sources = {i.get("source") for i in content.review_checklist}
        assert sources == {"deterministic", "model"}
        assert content.supplemental_clauses[0]["heading"] == "Copyleft"

    def test_endpoint_failure_degrades_to_deterministic_and_records_why(self, bundle) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="down", request=request)

        client = ChatClient(_settings(max_retries=1), transport=httpx.MockTransport(handler))
        content = draft(bundle, company_name="Acme", signatory_name="Ada", client=client)
        assert content.used_model is False
        assert len(content.prior_inventions) == 2
        assert any("fell back" in note for note in content.degraded)

    def test_entries_are_matched_by_name_not_position(self, bundle, openai_reply) -> None:
        replies = [
            json.dumps({"company_business": "b", "technical_field": "f", "executive_summary": "s"}),
            json.dumps(
                {
                    "prior_inventions": [
                        {
                            "repository": "rust-tool",
                            "title": "Rust",
                            "description": "d",
                            "carve_out_language": "c",
                            "relation_to_company_business": "r",
                        },
                        {
                            "repository": "earlier-vision",
                            "title": "Vision",
                            "description": "d",
                            "carve_out_language": "c",
                            "relation_to_company_business": "r",
                        },
                    ]
                }
            ),
            json.dumps({"review_checklist": []}),
        ]
        client = ChatClient(_settings(), transport=openai_reply(replies))
        content = draft(
            bundle, company_name="Acme", signatory_name="Ada", client=client, batch_size=10
        )
        by_repo = {pi["repository"]: pi["title"] for pi in content.prior_inventions}
        assert by_repo["rust-tool"] == "Rust"
        assert by_repo["earlier-vision"] == "Vision"


class TestCorporateDocument:
    def test_extracts_company_law_parties_and_sections(self, corporate_document: Path) -> None:
        context = parse_corporate_document(corporate_document)
        assert context.company_name == "Acme Robotics, Inc."
        assert context.governing_law == "State of Delaware"
        assert context.effective_date == "March 3, 2025"
        names = [p["name"] for p in context.parties]
        assert "Ada Lovelace" in names and "Grace Hopper" in names
        assert context.party_role("Ada Lovelace") == "Co-Founder and Chief Technology Officer"
        headings = [s.heading for s in context.sections]
        assert any("Intellectual Property" in h for h in headings)
        assert context.digest and context.digest.startswith("sha256:")

    def test_excerpt_for_model_is_bounded_and_relevant(self, corporate_document: Path) -> None:
        excerpt = parse_corporate_document(corporate_document).excerpt_for_model(limit=3000)
        assert excerpt and len(excerpt) <= 3000
        assert "Prior Inventions" in excerpt

    def test_missing_file_is_a_document_error(self, tmp_path: Path) -> None:
        with pytest.raises(DocumentError) as exc:
            parse_corporate_document(tmp_path / "nope.md")
        assert exc.value.remediation

    def test_thin_document_warns_instead_of_failing(self, tmp_path: Path) -> None:
        path = tmp_path / "thin.md"
        path.write_text("Just some notes.\n", encoding="utf-8")
        context = parse_corporate_document(path)
        assert context.warnings
        assert context.governing_law is None


class TestAssemblyAndRendering:
    def _document(self, bundle, tmp_path: Path, **kwargs):
        settings = Settings.load(cwd=tmp_path, env={})
        settings.document.company_name = kwargs.pop("company", "Acme, Inc.")
        settings.document.signatory_name = kwargs.pop("signatory", "Ada Lovelace")
        settings.document.governing_law = "the State of Delaware"
        settings.document.effective_date = "2026-01-15"
        content = draft(bundle, company_name="Acme, Inc.", signatory_name="Ada Lovelace")
        return assemble(bundle=bundle, content=content, settings=settings, **kwargs)

    def test_section_numbering_matches_the_cross_references(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        by_id = {s["id"]: s for s in doc.sections}
        assert by_id["definitions"]["number"] == 1
        assert by_id["confidentiality"]["number"] == 2
        assert by_id["assignment"]["number"] == 3
        assert by_id["prior-inventions"]["number"] == 4
        assert by_id["general"]["number"] == 10
        # Clause 4.3 is the incorporation licence that Section 4.4 and the
        # exhibits refer to by number.
        licence = by_id["prior-inventions"]["clauses"][2]
        assert licence["number"] == "4.3"
        assert "royalty-free" in licence["text"]

    def test_placeholders_are_all_filled(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        blob = json.dumps(doc.to_dict())
        assert "{company}" not in blob and "{signatory}" not in blob
        assert "Acme, Inc." in blob and "Ada Lovelace" in blob

    def test_non_solicitation_is_omitted_by_default_without_renumbering(
        self, bundle, tmp_path: Path
    ) -> None:
        default = self._document(bundle, tmp_path)
        section = next(s for s in default.sections if s["id"] == "restrictive-covenants")
        assert section["omitted"] is True
        assert section["number"] == 9
        assert section["clauses"][0]["text"] == "[Intentionally omitted.]"

        included = self._document(bundle, tmp_path, include_non_solicitation=True)
        section = next(s for s in included.sections if s["id"] == "restrictive-covenants")
        assert section["omitted"] is False
        assert "solicit" in section["clauses"][0]["text"]

    def test_missing_signatory_is_a_usage_error(self, bundle, tmp_path: Path) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        settings.document.company_name = "Acme"
        content = draft(bundle, company_name="Acme", signatory_name="x")
        with pytest.raises(UsageError) as exc:
            assemble(bundle=bundle, content=content, settings=settings)
        assert "--signatory" in (exc.value.remediation or "")

    def test_evidence_exhibit_records_commits_and_digest(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        evidence = doc.evidence
        assert evidence["analysis_digest"].startswith("sha256:")
        assert evidence["target"]["commit"] == "a" * 40
        assert {row["repository"] for row in evidence["prior_works"]} == {
            "earlier-vision",
            "rust-tool",
        }
        assert "No model was involved" in evidence["method"]

    def test_provenance_names_deterministic_when_no_model_ran(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        assert doc.provenance["model"] is None
        assert "prior_inventions" in doc.provenance["deterministic_fields"]

    def test_markdown_render_contains_the_agreement_and_all_exhibits(
        self, bundle, tmp_path: Path
    ) -> None:
        markdown = render_markdown(self._document(bundle, tmp_path))
        assert "# Proprietary Information and Inventions Agreement" in markdown
        assert "## Exhibit A -- Prior Inventions Excluded from Assignment" in markdown
        assert "## Exhibit B -- Deterministic Analysis Evidence" in markdown
        assert "## Exhibit C -- Review Checklist for Counsel" in markdown
        assert "Not legal advice." in markdown
        assert "earlier-vision" in markdown
        assert "{{" not in markdown  # no unrendered Jinja

    def test_html_render_is_self_contained(self, bundle, tmp_path: Path) -> None:
        html = render_html(self._document(bundle, tmp_path))
        assert html.startswith("<!DOCTYPE html>")
        assert "<style>" in html
        assert "http://" not in html.split("<style>")[1].split("</style>")[0]
        assert "<script" not in html
        assert "Ada Lovelace" in html

    def test_exhibit_a_extract_stands_alone(self, bundle, tmp_path: Path) -> None:
        exhibit = render_exhibit_a(self._document(bundle, tmp_path))
        assert exhibit.startswith("# Exhibit A -- Prior Inventions")
        # The extract stops before Exhibit B's heading. The phrase itself still
        # appears inside carve-out language, which cross-references it.
        assert "## Exhibit B" not in exhibit
        assert "earlier-vision" in exhibit

    def test_write_documents_writes_every_requested_format(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        out = tmp_path / "out"
        artifacts = write_documents(
            doc, output_dir=out, formats=["md", "json", "html"], bundle=bundle
        )
        kinds = {(a.kind, a.format) for a in artifacts}
        assert ("agreement", "md") in kinds
        assert ("exhibit-a", "md") in kinds
        assert ("analysis", "json") in kinds
        assert (out / "piia.md").is_file()
        assert json.loads((out / "piia.json").read_text())["title"] == doc.title
        assert all(a.bytes > 0 for a in artifacts)

    def test_unknown_format_is_rejected_with_the_valid_list(self, bundle, tmp_path: Path) -> None:
        doc = self._document(bundle, tmp_path)
        with pytest.raises(DocumentError) as exc:
            write_documents(doc, output_dir=tmp_path / "o", formats=["rtf"])
        assert "md" in (exc.value.remediation or "")

    def test_docx_round_trips_when_python_docx_is_available(self, bundle, tmp_path: Path) -> None:
        pytest.importorskip("docx")
        from piia.documents.docx import render_docx

        blob = render_docx(self._document(bundle, tmp_path))
        assert blob[:2] == b"PK"  # a zip container
        assert len(blob) > 5000

    def test_pdf_round_trips_when_weasyprint_is_available(self, bundle, tmp_path: Path) -> None:
        pytest.importorskip("weasyprint")
        blob = render_pdf(self._document(bundle, tmp_path))
        assert blob.startswith(b"%PDF-")
        assert len(blob) > 5000
