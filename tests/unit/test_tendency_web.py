import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from morning_radar.ai import FakeAIProvider
from morning_radar.ai.usage import atomic_json
from morning_radar.models import DailyBrief
from morning_radar.publishing.site_builder import SiteBuilder
from morning_radar.tendencies.core import run_core
from morning_radar.tendencies.core_demo import DEMO_TIME, build_core_demo, demo_decision
from morning_radar.tendencies.core_models import CoreDecision, SplitOutput
from morning_radar.tendencies.core_store import CoreStore, StaleRevision
from morning_radar.tendencies.core_validation import materialize
from morning_radar.tendencies.github_input import InboxConfig, ingest_issue, poll_github
from morning_radar.tendencies.inputs import read_evidence
from morning_radar.tendencies.web_cli import ensure_isolated
from morning_radar.tendencies.web_demo import SyntheticEditorProvider
from morning_radar.tendencies.web_input import WebRequest, accept_input, input_receipt
from morning_radar.tendencies.web_pages import build_pages, page_name
from morning_radar.tendencies.web_server import EditorApp

REPO = Path.cwd()
ORIGIN = "http://127.0.0.1:8768"


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "core"
    build_core_demo(REPO, root)
    return CoreStore(root)


@pytest.fixture
def github_page():
    return json.loads(
        Path("tests/fixtures/tendency_web/github-page.json").read_text(encoding="utf-8")
    )


def app_for(store):
    return EditorApp(store, REPO, origin=ORIGIN, passphrase="synthetic-local-auth", synthetic=True)


def authorized(app):
    headers = {"Host": "127.0.0.1:8768", "Origin": ORIGIN, "Content-Type": "application/json"}
    status, response_headers, _ = app.request(
        "POST", "/api/login", headers, json.dumps({"passphrase": "synthetic-local-auth"}).encode()
    )
    assert status == 200
    headers["Cookie"] = response_headers["Set-Cookie"].split(";")[0]
    status, _, raw = app.request("GET", "/api/session", headers)
    headers["X-CSRF-Token"] = json.loads(raw)["csrf"]
    return headers


def post(app, headers, **values):
    payload = {
        "operation_id": "synthetic-operation",
        "raw_text": "synthetic：甲站可能降低联网依赖。",
        "mode": "lines",
    } | values
    status, _, raw = app.request(
        "POST", "/api/submissions", headers, json.dumps(payload, ensure_ascii=False).encode()
    )
    return status, json.loads(raw)


def process(store, provider=None):
    times = [
        datetime.fromisoformat(json.loads(p.read_text())["recorded_at"])
        for p in (store.directory / "runs").glob("*.json")
    ]
    now = max([DEMO_TIME + timedelta(days=10), *times]) + timedelta(seconds=1)
    return run_core(
        store.root,
        config_root=REPO,
        provider=provider or SyntheticEditorProvider(),
        now=now,
    )


def test_real_adapter_multiple_submissions_core_and_restart(store):
    app = app_for(store)
    status, result = post(
        app,
        authorized(app),
        raw_text=("synthetic：甲站可能降低联网依赖。\nsynthetic：缓存有机会改变采样安排。"),
    )
    assert status == 201
    identity = result["submission_id"]
    record = input_receipt(store, identity)
    assert len(record["core"]["analysis"]) == 2
    assert {v["status"] for v in record["core"]["analysis"].values()} == {"unassessed"}
    process(store)
    assert {v["status"] for v in input_receipt(store, identity)["core"]["analysis"].values()} == {
        "recorded"
    }
    restarted = app_for(CoreStore(store.root))
    assert restarted.request("GET", "/api/session", authorized(app))[0] == 401
    status, _, html = restarted.request("GET", result["receipt_url"], {"Host": "127.0.0.1:8768"})
    assert status == 200 and "最初提交原文" in html.decode()
    assert "synthetic：缓存有机会" in html.decode()


def test_idempotency_retry_changed_payload_and_intentional_repeat(store):
    app, before = app_for(store), len(store.events())
    headers = authorized(app)
    one = post(app, headers)
    count = len(store.events())
    assert post(app, headers) == one and len(store.events()) == count > before
    assert post(app, headers, raw_text="更改同一操作的内容")[0] == 409
    two = post(app, headers, operation_id="synthetic-new-operation")
    assert two[1]["submission_id"] != one[1]["submission_id"]


def test_read_submit_race_correction_and_conflict_keeps_original(store):
    app = app_for(store)
    headers = authorized(app)
    first = next(iter(store.current().values()))
    request = {
        "action": "correction",
        "hypothesis_id": first.hypothesis_id,
        "expected_revision": first.revision_id,
        "raw_text": "synthetic：请说明负载限制。",
    }
    status, result = post(app, headers, **request)
    assert status == 201
    old_history = [r.model_dump() for r in store.revisions()]
    process(store)
    updated = store.current()[first.hypothesis_id]
    assert updated.revision_id != first.revision_id
    assert updated.decision.basis == "user_correction"
    status, conflict = post(app, headers, operation_id="late-page-operation", **request)
    assert status == 409
    saved = input_receipt(CoreStore(store.root), conflict["submission_id"])
    assert saved["acceptance"]["status"] == "conflict"
    assert saved["input"]["request"]["raw_text"] == request["raw_text"]
    assert saved["core"] is None
    assert [r.model_dump() for r in store.revisions()][: len(old_history)] == old_history
    assert input_receipt(store, result["submission_id"])["core"]["analysis"]


@pytest.mark.parametrize("change", ["host", "origin", "cookie", "csrf", "author", "unknown"])
def test_write_authorization_is_server_side(store, change):
    app = app_for(store)
    headers = authorized(app)
    values = {}
    if change == "host":
        headers["Host"] = "evil.example"
    elif change == "origin":
        headers["Origin"] = "https://evil.example"
    elif change == "cookie":
        headers.pop("Cookie")
    elif change == "csrf":
        headers["X-CSRF-Token"] = "forged"
    else:
        values[change] = "Tkkdy"
    before = len(store.events())
    assert post(app, headers, **values)[0] in {403, 409}
    assert len(store.events()) == before


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/",
        "https://user:pw@example.org/",
    ],
)
def test_unsafe_links_rejected_before_write(url):
    with pytest.raises(ValueError):
        WebRequest(operation_id="unsafe-link", raw_text="摘录", source_url=url)


def test_html_escaped_paths_rejected_and_no_diagnostics_leak(store):
    app = app_for(store)
    _, result = post(app, authorized(app), raw_text="<script>alert(1)</script>中文", mode="free")
    atomic_json(
        store.directory / "runs/failed.json",
        {
            "recorded_at": "2026-10-25T00:00:00+00:00",
            "run_id": "bad",
            "status": "failed",
            "error": "SECRET C:/private/path",
            "stages": {},
        },
    )
    host = {"Host": "127.0.0.1:8768"}
    status, headers, body = app.request("GET", result["receipt_url"], host)
    assert status == 200 and "Content-Security-Policy" in headers
    assert b"<script>alert" not in body and b"&lt;script&gt;" in body
    assert b"SECRET" not in body and b"C:/private" not in body
    for path in ["/../.local-write-passphrase", "/%2e%2e/config/app.yaml", "/data/foo.json"]:
        assert app.request("GET", path, host)[0] in {400, 404}


def test_original_survives_partial_split_and_processing_failure(store):
    app = app_for(store)
    _, result = post(app, authorized(app), raw_text="甲站观察。乙站观察。", mode="free")

    class Partial(FakeAIProvider):
        def split_hypotheses(self, context):
            return SplitOutput(
                items=[
                    {"text": "甲站观察。", "start": 0, "end": 5},
                    {"text": "错误文本", "start": 5, "end": 10},
                ]
            )

    process(store, Partial())
    saved = input_receipt(store, result["submission_id"])
    assert saved["core"]["receipt"]["status"] == "partial"
    accepted = saved["core"]["receipt"]["items"][0]["seed_id"]

    class Broken(FakeAIProvider):
        def split_hypotheses(self, context):
            raise ValueError("SECRET stack C:/private")

    process(store, Broken())
    saved = input_receipt(store, result["submission_id"])
    assert saved["core"]["receipt"]["items"][0]["seed_id"] == accepted
    status, _, body = app.request("GET", result["receipt_url"], {"Host": "127.0.0.1:8768"})
    assert status == 200 and "处理失败，原文保留" in body.decode()
    assert b"SECRET" not in body


def test_github_fixture_independent_workspace_and_core(tmp_path, github_page):
    config = InboxConfig(repository="Tkkdy/Morning-Radar", authorized_logins=["Tkkdy"])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=github_page))
    store = CoreStore(tmp_path / "clean-run")
    result = poll_github(
        store, config, token="synthetic-token", transport=transport, synthetic=True
    )
    assert result["scan_complete"] and result["results"][0]["status"] == "accepted"
    count = len(store.events())
    poll_github(
        CoreStore(store.root), config, token="synthetic-token", transport=transport, synthetic=True
    )
    assert len(store.events()) == count
    # A separate interpreter reads formal C inputs; no editor memory or hardcoded local source.
    code = (
        "import sys;sys.path.insert(0,'src');from pathlib import Path;"
        "from morning_radar.tendencies.core_store import CoreStore;"
        "print(len(CoreStore(Path(sys.argv[1])).seeds()))"
    )
    assert (
        subprocess.check_output([sys.executable, "-B", "-c", code, str(store.root)]).strip() == b"2"
    )
    result = process(store, FakeAIProvider())
    assert result["stages"]["discovery"]["status"] == "insufficient_evidence"


def test_github_author_editor_snapshot_edit_and_bad_form(tmp_path, github_page):
    store = CoreStore(tmp_path)
    config = InboxConfig(repository="Tkkdy/Morning-Radar", authorized_logins=["Tkkdy"])
    issue = github_page["data"]["repository"]["issues"]["nodes"][0]
    first = ingest_issue(store, issue, config, synthetic=True)
    original = input_receipt(store, first["submission_id"])["input"]["source"]["body"]
    issue = copy.deepcopy(issue)
    issue["lastEditedAt"] = "2026-10-10T12:00:00Z"
    issue["editor"] = {"login": "attacker"}
    issue["body"] = issue["body"].replace("甲站", "乙站")
    assert ingest_issue(store, issue, config)["status"] == "unauthorized"
    issue["editor"] = {"login": "Tkkdy"}
    second = ingest_issue(store, issue, config, synthetic=True)
    assert second["submission_id"] != first["submission_id"]
    assert input_receipt(store, first["submission_id"])["input"]["source"]["body"] == original
    issue["body"] = "not a supported form"
    assert ingest_issue(store, issue, config)["status"] == "invalid_input"
    assert store.events()[-1]["payload"]["source"]["body"] == issue["body"]


def test_github_cursor_failure_and_bounded_recovery(tmp_path, github_page):
    store = CoreStore(tmp_path)
    config = InboxConfig(
        repository="Tkkdy/Morning-Radar", authorized_logins=["Tkkdy"], maximum_pages=1
    )
    page = copy.deepcopy(github_page)
    page["data"]["repository"]["issues"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "cursor-one",
    }
    poll_github(
        store,
        config,
        token="fake",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)),
    )

    def failed(request):
        assert json.loads(request.content)["variables"]["after"] == "cursor-one"
        raise httpx.ConnectError("offline")

    with pytest.raises(httpx.ConnectError):
        poll_github(store, config, token="fake", transport=httpx.MockTransport(failed))
    assert json.loads((store.directory / "github-cursor.json").read_text())["after"] == "cursor-one"
    poll_github(
        store,
        config,
        token="fake",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=github_page)),
    )
    assert json.loads((store.directory / "github-cursor.json").read_text())["after"] is None


def test_history_static_export_navigation_and_status_compatibility(store, tmp_path):
    output = tmp_path / "site"
    brief = DailyBrief(date=DEMO_TIME.date(), timezone="Asia/Singapore", generated_at=DEMO_TIME)
    SiteBuilder(template_dir=REPO / "templates", output_dir=output).build(
        [brief], stylesheet=REPO / "site/assets/style.css", tendency_workspace=store.root
    )
    assert 'href="tendency/index.html"' in (output / "index.html").read_text(encoding="utf-8")
    assert json.loads((output / "status.json").read_text())["status"] == "SUCCESS"
    first, _, last = store.revisions()
    old = (output / "tendency" / page_name("r", first.revision_id)).read_text(encoding="utf-8")
    assert "这是历史版本" in old and "港区甲、港区乙" in old
    assert last.evidence[-1].content_version not in old or first.evidence[-1].content_version in old
    editor = (output / "tendency/edit.html").read_text(encoding="utf-8")
    assert "此静态预览不可写" in editor and 'id="submission"' not in editor
    assert "javascript:" not in old


def test_empty_failed_read_and_legacy_are_honest(tmp_path):
    store, output = CoreStore(tmp_path / "empty"), tmp_path / "pages"
    build_pages(store, output, repository=REPO)
    assert "尚无已保存" in (output / "index.html").read_text(encoding="utf-8")
    bad = store.directory / "transactions/bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("broken SECRET", encoding="utf-8")
    build_pages(store, output, repository=REPO)
    content = (output / "index.html").read_text(encoding="utf-8")
    assert "读取失败" in content and "尚无已保存" not in content and "SECRET" not in content


def test_branch_lineage_survives_child_update_and_parent_changes(store):
    original = store.revisions()[0]
    evidence, _ = read_evidence(store.root, store, through=DEMO_TIME + timedelta(days=1))
    context = {"evidence": [e.model_dump(mode="json") for e in evidence], "targets": []}
    draft = demo_decision(context, "narrowed")
    # Branch from the saved broad hypothesis in an independent store, then update it.
    target = CoreStore(store.root / "branch")
    target.append({"revisions": [original.model_dump(mode="json")]}, operation_id="initial")
    draft.update(
        kind="split",
        parent_hypothesis_id=original.hypothesis_id,
        parent_revision_id=original.revision_id,
    )
    child = materialize(
        CoreDecision.model_validate(draft),
        evidence={e.evidence_id: e for e in evidence},
        current=target.current(),
        seeds={},
        now=DEMO_TIME + timedelta(days=1),
        run_id="branch",
        analysis_version="synthetic",
    )
    target.append({"revisions": [child.model_dump(mode="json")]}, operation_id="branch")
    target.append(
        {"revisions": [store.revisions()[1].model_dump(mode="json")]},
        operation_id="parent-advanced",
    )
    draft = child.decision.model_dump()
    draft.update(
        kind="analysis_reassessment",
        basis="analysis_reassessment",
        hypothesis_id=child.hypothesis_id,
        previous_revision_id=child.revision_id,
        parent_hypothesis_id=None,
        parent_revision_id=None,
        changed_parts=["mechanism"],
    )
    draft["hypothesis"]["mechanism"] += " 分析修订：负载边界需要另测。"
    updated = materialize(
        CoreDecision.model_validate(draft),
        evidence={e.evidence_id: e for e in evidence},
        current=target.current(),
        seeds={},
        now=DEMO_TIME + timedelta(days=2),
        run_id="update",
        analysis_version="synthetic",
    )
    assert updated.parent_revision_id == original.revision_id
    target.append({"revisions": [updated.model_dump(mode="json")]}, operation_id="updated")
    forged = updated.model_copy(
        update={"parent_hypothesis_id": None, "previous_revision_id": updated.revision_id}
    )
    with pytest.raises(StaleRevision):
        target.append({"revisions": [forged.model_dump(mode="json")]}, operation_id="forged")
    output = store.root / "branch-preview"
    build_pages(target, output, repository=REPO)
    assert page_name("r", original.revision_id) in (
        output / page_name("h", child.hypothesis_id)
    ).read_text(encoding="utf-8")


def test_cli_protects_production_paths():
    for path in [REPO, REPO / "data", REPO / "site/output", REPO.parent]:
        with pytest.raises(ValueError):
            ensure_isolated(path, REPO)


def test_optional_link_reaches_core_as_opinion_not_fact(tmp_path):
    store = CoreStore(tmp_path)
    saved = accept_input(
        store,
        WebRequest(
            operation_id="optional-link",
            raw_text="我的假设",
            source_url="https://example.org/original",
        ),
        author="owner",
        source={"channel": "test"},
    )
    material = saved["core"]["submission"]["materials"][0]
    assert material["url"] == "https://example.org/original"
    assert material["kind"] == "user_hypothesis"


def test_server_root_redirects_to_canonical_relative_asset_base(tmp_path):
    app = app_for(CoreStore(tmp_path))
    status, headers, _ = app.request("GET", "/", {"Host": "127.0.0.1:8768"})
    assert status == 302 and headers["Location"] == "/tendency/index.html"


def test_independent_core_recovers_crash_after_ingress(tmp_path, monkeypatch):
    import morning_radar.tendencies.web_input as adapter

    store = CoreStore(tmp_path)
    original = adapter.submit

    def interrupted(*args, **kwargs):
        raise OSError("synthetic interruption")

    monkeypatch.setattr(adapter, "submit", interrupted)
    with pytest.raises(OSError):
        accept_input(
            store,
            WebRequest(
                operation_id="interrupted-input", raw_text="甲判断。\n乙判断。", mode="lines"
            ),
            author="owner",
            source={"channel": "test"},
            synthetic=True,
            now=DEMO_TIME,
        )
    assert len(store.events()) == 1 and not store.submissions()
    monkeypatch.setattr(adapter, "submit", original)
    result = process(CoreStore(tmp_path), FakeAIProvider())
    assert result["input_recovery"]["recovered"] == 1
    assert len(store.seeds()) == 2
    assert process(CoreStore(tmp_path), FakeAIProvider())["input_recovery"]["recovered"] == 0
