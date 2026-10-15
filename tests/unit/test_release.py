from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from morning_radar.ai.budget import AIBudgetExceeded
from morning_radar.ai.usage import PriceQuote, UsageRecorder, usage_summary
from morning_radar.release.accounting import Accounting, ReleasePolicy
from morning_radar.release.archive import ArchiveConflict, GitArchive, archive_files, git
from morning_radar.release.coordinator import ReleaseCoordinator, read
from morning_radar.release.delivery import Pages, WxSender, deploy, notify
from morning_radar.release.demo import demo
from morning_radar.tendencies.core_store import CoreStore

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("release")
    result = demo(REPO, root)
    return root, result


def clone(source, target):
    git(source, "clone", "--no-hardlinks", str(source), str(target))
    return ReleaseCoordinator(
        target, archive=GitArchive(target, allow_synthetic=True), synthetic=True
    )


def pages_for(coordinator, identity, *, status="succeed", wrong=False, failure=False):
    manifest = coordinator.verify(identity)
    package = coordinator.directory / "packages" / identity / "site"
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if request.method == "POST":
            if failure:
                raise httpx.ReadTimeout("synthetic", request=request)
            body = json.loads(request.content)
            assert body["pages_build_version"] == identity
            return httpx.Response(200, json={"id": "external-deployment-123"})
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"status": status})
        assert "Authorization" not in request.headers
        name = request.url.path.removeprefix("/Morning-Radar/")
        assert name in manifest["files"]
        return httpx.Response(200, content=b"wrong" if wrong else (package / name).read_bytes())

    return Pages(
        "Tkkdy/Morning-Radar",
        "synthetic",
        "https://example.org/Morning-Radar",
        transport=httpx.MockTransport(respond),
    ), calls


def test_real_modules_freeze_and_fresh_runner_restores_only_git(generated, tmp_path):
    root, result = generated
    c = clone(root, tmp_path / "runner-two")
    identity = result["release_id"]
    manifest = c.verify(identity)
    assert not (c.root / ".tmp").exists()
    assert not (c.root / "site").exists()
    assert manifest["synthetic"] is True
    assert manifest["batch_id"]
    assert manifest["brief_versions"]
    assert not CoreStore(c.root).submissions()
    assert not CoreStore(c.root).seeds()
    assert not manifest["tendency_revisions"]
    assert not CoreStore(c.root).draft_revisions()
    assert read(c.root / "data/tendency-public/v1/confirmed.json")["private_state_requires_restore"]
    assert all(
        v["status"] not in {"running", "not_run", "failed"} for v in manifest["stages"].values()
    ), manifest["stages"]
    c.export(identity, tmp_path / "export")
    assert (tmp_path / "export/tendency/history.html").exists()
    pages, calls = pages_for(c, identity)
    assert (
        deploy(c, identity, pages, artifact_id=42, oidc="synthetic", delay=0)["status"]
        == "confirmed"
    )
    assert any("/Morning-Radar/tendency/" in call for call in calls)
    from morning_radar.intake.publish import PublishStore

    assert PublishStore(c.root / "data/state/publish.json").get(manifest["business_date"]).deployed
    c2 = clone(c.root, tmp_path / "runner-three")
    assert c2.context(identity)["deployment"]["id"] == "external-deployment-123"
    before = len(calls)
    deploy(c2, identity, pages, artifact_id=43, oidc="synthetic", delay=0)
    assert len(calls) == before


def test_deploy_unknown_failure_reconcile_and_package_integrity(generated, tmp_path):
    c = clone(generated[0], tmp_path / "runner")
    identity = generated[1]["release_id"]
    pages, calls = pages_for(c, identity, failure=True)
    assert (
        deploy(c, identity, pages, artifact_id=1, oidc="synthetic", delay=0)["status"] == "unknown"
    )
    assert (
        deploy(c, identity, pages, artifact_id=2, oidc="synthetic", delay=0)["status"] == "unknown"
    )
    assert len(calls) == 1
    with pytest.raises(ValueError, match="confirmed"):
        notify(
            c, identity, sender=None, recipients=["synthetic"], public_url="https://example.org/"
        )
    wrong, _ = pages_for(c, identity, wrong=True)
    assert deploy(c, identity, wrong, reconcile_id="actual-id", polls=1)["status"] == "unknown"
    failed, _ = pages_for(c, identity, status="deployment_failed")
    assert deploy(c, identity, failed, polls=1)["status"] == "failed"
    good, _ = pages_for(c, identity)
    assert deploy(c, identity, good, artifact_id=3, oidc="synthetic")["status"] == "confirmed"
    p = c.directory / "packages" / identity / "site/index.html"
    p.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        c.verify(identity)


def test_notify_recipient_partial_unknown_and_same_day_rerun(generated, tmp_path):
    c = clone(generated[0], tmp_path / "runner")
    identity = generated[1]["release_id"]
    pages, _ = pages_for(c, identity)
    deploy(c, identity, pages, artifact_id=1, oidc="synthetic")
    calls = []

    def respond(request):
        body = json.loads(request.content)
        uid = body["uids"][0]
        calls.append(uid)
        if uid == "unknown":
            raise httpx.ReadTimeout("uncertain", request=request)
        if uid == "rejected" and calls.count(uid) == 1:
            return httpx.Response(200, json={"success": False, "code": 1001})
        return httpx.Response(
            200,
            json={
                "success": True,
                "code": 1000,
                "data": [{"uid": uid, "code": 1000, "sendRecordId": 123}],
            },
        )

    sender = WxSender("synthetic", transport=httpx.MockTransport(respond))
    state = notify(
        c,
        identity,
        sender=sender,
        recipients=["ok", "unknown", "rejected"],
        public_url="https://example.org/",
    )
    assert sorted(r["status"] for r in state["recipients"].values()) == [
        "failed",
        "sent",
        "unknown",
    ]
    c2 = clone(c.root, tmp_path / "runner2")
    notify(
        c2,
        identity,
        sender=sender,
        recipients=["ok", "unknown", "rejected"],
        public_url="https://example.org/",
    )
    assert calls == ["ok", "unknown", "rejected", "rejected"]
    assert "ok" not in json.dumps(
        read(c2.directory / "notifications" / (c2.context(identity)["business_date"] + ".json"))
    )


def test_archive_conflict_keeps_commit_and_no_private_files(generated, tmp_path):
    root = generated[0]
    remote = tmp_path / "remote.git"
    git(root, "clone", "--bare", str(root), str(remote))
    one, two = tmp_path / "one", tmp_path / "two"
    a, b = clone(remote, one), clone(remote, two)
    for c, value in [(a, 1), (b, 2)]:
        p = c.root / "data/releases/v1/example.json"
        p.write_text(json.dumps({"value": value}))
    GitArchive(one, remote="origin", allow_synthetic=True)("first")
    with pytest.raises(ArchiveConflict, match="preserve commit"):
        GitArchive(two, remote="origin", allow_synthetic=True)("second")
    assert git(two, "status", "--short") == ""
    (two / ".env").write_text("synthetic secret")
    (two / "data/tendency-core/v2").mkdir(parents=True, exist_ok=True)
    (two / "data/tendency-core/v2/private.lock").write_text("lock")
    assert not any(name.endswith((".lock", ".env")) for name in archive_files(two))


def test_cost_across_modules_restarts_unknowns_and_reservations(tmp_path):
    price = PriceQuote(
        model="synthetic",
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        source_url="https://example.org/price",
        verified_on="2026-10-10",
    )
    policy = ReleasePolicy(
        price=price, daily_limit_cny=Decimal(".1"), per_call_reserve_cny=Decimal(".02")
    )
    checkpoints = []
    account = Accounting(tmp_path, "2026-10-10", checkpoints.append, policy)
    account.archive = lambda reason: checkpoints.append(reason)
    a = UsageRecorder(tmp_path, business_date=account.day, module="daily", accounting=account)
    r = a.start(
        provider="synthetic",
        model="synthetic",
        task="test",
        request_characters=100,
        maximum_output_tokens=100,
    )
    account.active.clear()
    with pytest.raises(AIBudgetExceeded, match="unavailable"):
        a.start(
            provider="synthetic",
            model="synthetic",
            task="test",
            request_characters=100,
            maximum_output_tokens=100,
        )
    finished = a.finish(r, usage={"prompt_tokens": 20000, "completion_tokens": 0}, state="received")
    a.import_record(finished)
    b = UsageRecorder(tmp_path, business_date=account.day, module="tendency", accounting=account)
    r2 = b.start(
        provider="synthetic",
        model="synthetic",
        task="test",
        request_characters=100,
        maximum_output_tokens=100,
    )
    b.finish(r2, usage={"prompt_tokens": 70000, "completion_tokens": 0}, state="received")
    summary = usage_summary(tmp_path, account.day)
    assert summary["physical_calls"] == 2
    assert Decimal(summary["total_cost_cny"]) == Decimal(".09")
    with pytest.raises(AIBudgetExceeded, match="reservation"):
        b.start(
            provider="synthetic",
            model="synthetic",
            task="test",
            request_characters=100,
            maximum_output_tokens=100,
        )
    assert checkpoints
    next_day = Accounting(tmp_path, "2026-10-11", lambda _: None, policy)
    next_day.before()


def test_synthetic_blocked_for_production_archive(generated):
    with pytest.raises(ValueError, match="Synthetic"):
        GitArchive(generated[0])("must not publish")


def test_inbox_duplicate_stale_source_and_correction_cas(generated, tmp_path):
    from morning_radar.tendencies.github_input import ingest_issue, load_inbox

    c = clone(generated[0], tmp_path / "runner")
    # Explicit synthetic private backup restore for CAS tests; not a public Git feature.
    shutil.copytree(generated[0] / "data/tendency-core", c.root / "data/tendency-core")
    store = CoreStore(c.root)
    config = load_inbox(c.root / "config/tendency_web.yaml")
    issue = read(REPO / "tests/fixtures/tendency_web/github-page.json")["data"]["repository"][
        "issues"
    ]["nodes"][0]
    issue["id"] = "new-synthetic-object"
    first = ingest_issue(store, issue, config, synthetic=True)
    count = len(store.events())
    assert ingest_issue(store, issue, config, synthetic=True) == first
    assert len(store.events()) == count
    issue = {**issue, "lastEditedAt": "2026-10-11T12:00:00Z", "editor": {"login": "Tkkdy"}}
    ingest_issue(store, issue, config, synthetic=True)
    issue["lastEditedAt"] = "2026-10-11T11:00:00Z"
    assert ingest_issue(store, issue, config, synthetic=True)["status"] == "stale_source"
    target = next(iter(store.current().values()))
    issue.update(
        id="correction-object",
        lastEditedAt="2026-10-12T12:00:00Z",
        body="### 原始输入\n\nsynthetic 纠偏\n\n### 操作\n\n纠偏"
        "\n\n### 假设 ID\n\n" + target.hypothesis_id + "\n\n### 期望修订 ID\n\nstale-version",
    )
    assert ingest_issue(store, issue, config, synthetic=True)["status"] == "conflict"
    c.archive("receipt conflict")
    fresh = clone(c.root, tmp_path / "new-runner")
    assert not CoreStore(fresh.root).current()
    assert CoreStore(fresh.root).public_current() == store.public_current()
    assert any(
        e["acceptance"] and e["acceptance"]["status"] == "conflict"
        for e in store.events()
    )
    assert not CoreStore(fresh.root).events()


def test_intent_survives_lost_notification_result_archive(generated, tmp_path):
    c = clone(generated[0], tmp_path / "runner")
    identity = generated[1]["release_id"]
    pages, _ = pages_for(c, identity)
    deploy(c, identity, pages, artifact_id=1, oidc="synthetic")
    archive = c.archive

    def interrupted(reason):
        if reason == "notification per-recipient result":
            raise RuntimeError("synthetic process interruption")
        return archive(reason)

    c.archive = interrupted
    calls = []

    def response(request):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "success": True,
                "code": 1000,
                "data": [{"uid": "recipient", "code": 1000, "sendRecordId": 77}],
            },
        )

    sender = WxSender("synthetic", transport=httpx.MockTransport(response))
    with pytest.raises(RuntimeError):
        notify(
            c, identity, sender=sender, recipients=["recipient"], public_url="https://example.org/"
        )
    fresh = clone(c.root, tmp_path / "fresh")
    result = notify(
        fresh, identity, sender=sender, recipients=["recipient"], public_url="https://example.org/"
    )
    assert len(calls) == 1
    assert next(iter(result["recipients"].values()))["status"] == "unknown"


def test_confirmed_deploy_receipt_lost_reconciles_without_new_post(generated, tmp_path):
    c = clone(generated[0], tmp_path / "runner")
    identity = generated[1]["release_id"]
    archive = c.archive

    def interrupted(reason):
        if reason == "verified public deployment":
            raise RuntimeError("synthetic receipt archive interruption")
        return archive(reason)

    c.archive = interrupted
    pages, calls = pages_for(c, identity)
    with pytest.raises(RuntimeError):
        deploy(c, identity, pages, artifact_id=1, oidc="synthetic")
    fresh = clone(c.root, tmp_path / "fresh")
    assert fresh.context(identity)["deployment"]["status"] == "pending"
    assert deploy(fresh, identity, pages)["status"] == "confirmed"
    assert sum(call.endswith("/pages/deployments") for call in calls) == 1


def test_cross_midnight_cutoff_and_later_package_retains_old_history(generated, tmp_path):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from morning_radar.collectors import CollectionResult
    from morning_radar.release.demo import ReleaseDemoProvider
    from morning_radar.tendencies.web_input import WebRequest, accept_input

    c = clone(generated[0], tmp_path / "runner")
    # Runtime assets are deployed code, not state; a new runner checks them out.
    (c.root / "site/assets").mkdir(parents=True)
    (c.root / "site/assets/style.css").write_bytes((REPO / "site/assets/style.css").read_bytes())
    c.provider = ReleaseDemoProvider()
    original = generated[1]["release_id"]
    pages, _ = pages_for(c, original)
    deploy(c, original, pages, artifact_id=1, oidc="synthetic")
    old = c.verify(original)
    clock = datetime(2026, 10, 11, 15, 59, 59, tzinfo=UTC)
    store = CoreStore(c.root)
    late = accept_input(
        store,
        WebRequest(
            operation_id="after-cutoff-synthetic", raw_text="synthetic 截稿后新判断", mode="lines"
        ),
        author="synthetic",
        source={},
        synthetic=True,
        now=clock + timedelta(seconds=2),
    )
    c.archive("late input for next release")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "nameWithOwner": "Tkkdy/Morning-Radar",
                        "issues": {
                            "nodes": [],
                            "pageInfo": {"endCursor": None, "hasNextPage": False},
                        },
                    }
                }
            },
        )
    )
    with patch(
        "morning_radar.intake.service._production_collect",
        return_value=(CollectionResult(), [], []),
    ):
        result = c.generate(
            token="synthetic", transport=transport, now=clock, identity="next-synthetic-release"
        )
    assert result["business_date"] == "2026-10-11"
    assert late["submission_id"] not in result["submission_versions"]
    from morning_radar.tendencies.web_pages import page_name

    assert not (
        c.directory
        / "packages/next-synthetic-release/site/tendency"
        / page_name("s", late["submission_id"])
    ).exists()
    core_run = read(
        c.root / "data/tendency-core/v2/runs" / (result["stages"]["tendency"]["run_id"] + ".json")
    )
    assert all(
        late["submission_id"] not in json.dumps(stage.get("request", {}))
        for stage in core_run["stages"].values()
    )
    new = c.verify(result["release_id"])
    assert new["previous_valid_release"] == original
    assert set(old["tendency_revisions"].values()) <= {
        r.revision_id for r in CoreStore(c.root).revisions()
    }
    assert set(old["files"]) <= set(new["files"])
    assert c.verify(original) == old
    # Same release command does not re-ingest or generate.
    assert c.generate(token="unused", identity=result["release_id"]) == c.context(
        result["release_id"]
    )


def test_no_verified_price_means_no_new_physical_request(tmp_path):
    account = Accounting(tmp_path, "2026-10-10", lambda _: None, ReleasePolicy())
    recorder = UsageRecorder(
        tmp_path, business_date=account.day, module="daily", accounting=account
    )
    with pytest.raises(AIBudgetExceeded, match="price"):
        recorder.start(
            provider="synthetic",
            model="unknown",
            task="test",
            request_characters=1,
            maximum_output_tokens=1,
        )
    assert usage_summary(tmp_path, account.day)["physical_calls"] == 0


def test_partial_inbox_failure_preserves_other_objects_and_cursor(tmp_path):
    from morning_radar.tendencies.github_input import InboxConfig, poll_github

    fixture = read(REPO / "tests/fixtures/tendency_web/github-page.json")
    nodes = fixture["data"]["repository"]["issues"]["nodes"]
    bad = {**nodes[0], "id": "broken", "number": 102, "createdAt": "not-a-time"}
    good2 = {**nodes[0], "id": "good2", "number": 103}
    nodes.extend([bad, good2])
    fixture["data"]["repository"]["issues"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "later",
    }
    store = CoreStore(tmp_path)
    result = poll_github(
        store,
        InboxConfig(repository="Tkkdy/Morning-Radar", authorized_logins=["Tkkdy"]),
        token="synthetic",
        synthetic=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=fixture)),
    )
    assert result["scan_complete"] is False
    assert result["failed_objects"]
    assert len(store.submissions()) == 2
    assert not (store.directory / "github-cursor.json").exists()


def test_explicit_resend_has_separate_idempotency_and_rollback_uses_frozen_bytes(
    generated, tmp_path
):
    c = clone(generated[0], tmp_path / "runner")
    identity = generated[1]["release_id"]
    pages, calls = pages_for(c, identity)
    deploy(c, identity, pages, artifact_id=1, oidc="synthetic")
    manifest = c.verify(identity)
    sent = []

    def response(request):
        sent.append(1)
        return httpx.Response(
            200,
            json={
                "success": True,
                "code": 1000,
                "data": [{"uid": "recipient", "code": 1000, "sendRecordId": 77}],
            },
        )

    sender = WxSender("synthetic", transport=httpx.MockTransport(response))
    for auth in [None, None, "approved-synthetic-resend", "approved-synthetic-resend"]:
        notify(
            c,
            identity,
            sender=sender,
            recipients=["recipient"],
            public_url="https://example.org/",
            authorization_id=auth,
        )
    assert len(sent) == 2
    assert (
        deploy(c, identity, pages, artifact_id=2, oidc="synthetic", redeploy=True)["status"]
        == "confirmed"
    )
    assert sum(url.endswith("/pages/deployments") for url in calls) == 2
    assert c.verify(identity) == manifest
    assert len(c.context(identity)["deployment_history"]) == 1
    assert len(sent) == 2


def test_daily_reserves_tendency_and_continuity_then_releases_capacity(tmp_path):
    policy = ReleasePolicy(
        daily_limit_cny=Decimal(".1"),
        per_call_reserve_cny=Decimal(".02"),
        tendency_reserve_cny=Decimal(".06"),
        continuity_reserve_cny=Decimal(".03"),
    )
    account = Accounting(tmp_path, "2026-10-10", lambda _: None, policy)
    account.phase = "daily"
    with pytest.raises(AIBudgetExceeded, match="reservation"):
        account.before()
    account.phase = "tendency"
    assert account.before() == Decimal(".02")
    account.phase = "deep_continuity"
    assert account.before() == Decimal(".02")


def test_cache_usage_or_cache_price_missing_is_unavailable(tmp_path):
    recorder = UsageRecorder(tmp_path, business_date="2026-10-10", module="daily")
    quote = PriceQuote(
        model="synthetic",
        input_per_million=2,
        output_per_million=3,
        source_url="https://example.org/synthetic",
        verified_on="2026-10-10",
    )
    record = recorder.start(
        provider="deepseek", model="synthetic", task="test", request_characters=100
    )
    usage = {"prompt_tokens": 100, "completion_tokens": 10}
    assert recorder.finish(record, usage=usage, state="received", price=quote)["cost_cny"] is None
    usage.update(cache_hit_tokens=50, cache_miss_tokens=50)
    assert recorder.finish(record, usage=usage, state="received", price=quote)["cost_cny"] is None
    priced = quote.model_copy(update={"cached_input_per_million": Decimal("1")})
    assert Decimal(
        recorder.finish(record, usage=usage, state="received", price=priced)["cost_cny"]
    ) == Decimal(".00018")


def test_complete_release_with_sufficient_synthetic_source_excerpts(tmp_path, monkeypatch):
    """The real coordinator must complete when the external inputs satisfy prerequisites."""
    from morning_radar.collectors import FixtureCollector

    original = FixtureCollector.collect

    def sufficient(self):
        items = original(self)
        for item in items:
            # Authored synthetic source detail, not a forced health/status result.
            item.content_excerpt = (
                "synthetic source conditions: this fixture verifies pipeline structure only. "
                "The reported change has bounded scope, requires explicit approval and "
                "finite retries, and has not been evaluated in real deployments. "
            ) * 3
        return items

    monkeypatch.setattr(FixtureCollector, "collect", sufficient)
    root = tmp_path / "complete-release"
    result = demo(REPO, root)
    coordinator = ReleaseCoordinator(
        root, archive=GitArchive(root, allow_synthetic=True), synthetic=True
    )
    manifest = coordinator.verify(result["release_id"])
    assert manifest["delivery_status"] == "complete"
    assert manifest["stages"]["daily"]["status"] == "completed"
    assert manifest["stages"]["tendency"]["status"] == "completed"
    assert manifest["stages"]["collection"]["status"] == "completed"
    assert manifest["stages"]["deep_continuity"]["status"] == "no_ai_trigger"
    assert manifest["cost"]["physical_calls"] == 0
