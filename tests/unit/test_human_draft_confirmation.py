"""Isolated synthetic fixtures: no real candidate is human-confirmed by tests."""
import json
from pathlib import Path

import pytest
from test_grounded_review import NOW, assessment, item, provider, run_review

from morning_radar.tendencies.core_export import export_core
from morning_radar.tendencies.core_store import CoreStore, StaleRevision
from morning_radar.tendencies.web_input import ConfirmationRequest, confirm_draft
from morning_radar.tendencies.web_pages import build_pages, page_name
from morning_radar.tendencies.web_server import EditorApp


def decide(store, revision, action="confirm", operation="synthetic-confirm-1"):
    return confirm_draft(store, ConfirmationRequest(
        operation_id=operation, action=action, **store.confirmation_binding(revision)),
        author="synthetic-test-owner", now=NOW)


@pytest.mark.parametrize("verdict", ["approved", "rejected", "uncertain"])
def test_review_opinion_is_private_until_human_confirmation(tmp_path, verdict):
    store = CoreStore(tmp_path)
    draft = item(store)
    def judge(context):
        return {"assessments": [assessment(context["candidates"][0], verdict)]}
    run_review(store, provider(judge), [draft])
    revision = store.current()[draft["revision"]["hypothesis_id"]]
    assert not store.public_current() and not export_core(store)["history"]
    output = tmp_path / "site"
    build_pages(store, output, repository=Path("."), local_write=True)
    detail = output / page_name("r", revision.revision_id)
    assert revision.decision.hypothesis.claim in detail.read_text(encoding="utf-8")
    build_pages(store, output, repository=Path("."))
    assert all(revision.decision.hypothesis.claim not in p.read_text(encoding="utf-8")
               for p in output.glob("*.html"))
    decide(store, revision)
    reopened = CoreStore(tmp_path)
    assert reopened.public_current()[revision.hypothesis_id] == revision
    assert reopened.human_decisions()[revision.revision_id]["author"] == "synthetic-test-owner"


def test_new_draft_cannot_inherit_confirmation_or_replace_public_version(tmp_path):
    store = CoreStore(tmp_path)
    old_item = item(store)
    old = store.current()[old_item["revision"]["hypothesis_id"]]
    decide(store, old)
    next_item = item(store, previous=old, suffix="-next", text="synthetic：新的条件结构研究假设")
    new = store.current()[next_item["revision"]["hypothesis_id"]]
    assert store.public_current()[old.hypothesis_id] == old
    with pytest.raises(StaleRevision):
        decide(store, old, operation="synthetic-stale-confirm")
    bad = store.confirmation_binding(new) | {"evidence_sha256": "0" * 64}
    with pytest.raises(StaleRevision):
        confirm_draft(store, ConfirmationRequest(operation_id="synthetic-wrong-evidence",
                      action="confirm", **bad), author="synthetic-test-owner", now=NOW)
    decide(store, new, action="return", operation="synthetic-return")
    assert store.public_current()[old.hypothesis_id] == old
    decide(store, new, operation="synthetic-confirm-new")
    assert store.public_current()[old.hypothesis_id] == new
    assert len(store.public_revisions()) == 2


def test_local_confirmation_requires_session_and_csrf_and_persists(tmp_path):
    store = CoreStore(tmp_path)
    draft = item(store)
    revision = store.current()[draft["revision"]["hypothesis_id"]]
    app = EditorApp(store, Path("."), origin="http://127.0.0.1:8768",
                    passphrase="synthetic-only", synthetic=True)
    headers = {"Host": "127.0.0.1:8768", "Origin": app.origin,
               "Content-Type": "application/json"}
    body = json.dumps(dict(operation_id="synthetic-http-confirm", action="confirm",
                           **store.confirmation_binding(revision))).encode()
    assert app.request("POST", "/api/confirmations", headers, body)[0] == 403
    _, _, public = app.request("GET", "/public/tendency/index.html", headers)
    assert revision.decision.hypothesis.claim not in public.decode("utf-8")
    code, response_headers, _ = app.request("POST", "/api/login", headers,
                                            b'{"passphrase":"synthetic-only"}')
    assert code == 200
    headers["Cookie"] = response_headers["Set-Cookie"].split(";")[0]
    _, _, response = app.request("GET", "/api/session", headers)
    headers["X-CSRF-Token"] = json.loads(response)["csrf"]
    assert app.request("POST", "/api/confirmations", headers, body)[0] == 201
    assert app.request("POST", "/api/confirmations", headers, body)[0] == 201
    assert len(store.human_decisions()) == 1
    _, _, public = app.request("GET", "/public/tendency/index.html", headers)
    assert revision.decision.hypothesis.claim in public.decode("utf-8")
    assert CoreStore(tmp_path).public_current()[revision.hypothesis_id] == revision


def test_invalid_quote_cannot_become_a_readable_draft(tmp_path):
    from test_grounded_review import TRANSACTION

    from morning_radar.tendencies.core_models import Revision
    from morning_radar.tendencies.grounded_review import candidate, review_version
    revision = Revision.model_validate(TRANSACTION["revisions"][0])
    revision.decision.citations[0].quote = "这段文字并未存在于对应的短摘录中。"
    store = CoreStore(tmp_path)
    with pytest.raises(ValueError, match="Invalid draft evidence quote"):
        store.append({"type": "review_candidate", "revision": revision.model_dump(mode="json"),
                      "request": candidate(revision, None, review_version(Path("prompts")))},
                     operation_id="synthetic-invalid-citation")
    assert not store.events()
