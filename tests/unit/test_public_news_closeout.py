"""Local-only regression of confirmed publication and bounded news degradation."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_grounded_review import item as candidate
from test_human_draft_confirmation import decide
from test_story_builder import NOW, item

from morning_radar.ai import AIOutputError, FakeAIProvider
from morning_radar.ai.models import ClassificationBatch, MergedStoryDraft
from morning_radar.ai.request_payload import merge_story_payload
from morning_radar.ai.structured_output import prepare_structured_output
from morning_radar.briefing.generator import (
    SECTION_NAMES,
    BriefLimits,
    generate_daily_brief,
    reuse_archived_cards,
)
from morning_radar.editorial.evaluator import validate_editorial_batch
from morning_radar.editorial.models import EditorialDecisionBatch
from morning_radar.processing.grouping import bounded_event_groups
from morning_radar.processing.story_builder import (
    build_stories,
    build_story,
    input_version_conflicts,
)
from morning_radar.release import archive
from morning_radar.tendencies.core_export import export_core, save_public_projection
from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.web_input import ConfirmationRequest, confirm_draft
from morning_radar.tendencies.web_pages import build_pages


def test_archive_projects_confirmed_content_not_transactions_or_private_notes(
    tmp_path, monkeypatch
):
    store = CoreStore(tmp_path)
    first = candidate(store, text="合成：已经确认的研究假设")
    revision = store.current()[first["revision"]["hypothesis_id"]]
    confirm_draft(
        store,
        ConfirmationRequest(
            operation_id="synthetic-owner-confirm",
            action="confirm",
            note="PRIVATE_CONFIRMATION_NOTE",
            **store.confirmation_binding(revision),
        ),
        author="synthetic-owner",
        now=NOW,
    )
    candidate(store, text="PRIVATE_UNCONFIRMED_BODY", previous=revision, suffix="-next")
    (store.directory / "review-response.json").write_text(
        json.dumps({"raw_response": "PRIVATE_REVIEW_RESPONSE"}), encoding="utf-8"
    )
    calls = []

    def fake_git(root, *args):
        calls.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return "synthetic-sha"
        return ""

    monkeypatch.setattr(archive, "git", fake_git)
    archive.GitArchive(tmp_path, allow_synthetic=True)("synthetic boundary")
    files = archive.archive_files(tmp_path)
    assert files == ["data/tendency-public/v1/confirmed.json"]
    wire = "\n".join((tmp_path / name).read_text(encoding="utf-8") for name in files)
    assert "PRIVATE_UNCONFIRMED_BODY" not in wire and "PRIVATE_CONFIRMATION_NOTE" not in wire
    assert "PRIVATE_REVIEW_RESPONSE" not in wire
    assert any(call[:2] == ("add", "--") for call in calls)
    assert not any("push" in call for call in calls)
    restored = tmp_path / "restored"
    for name in files:
        dest = restored / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path / name, dest)
    public = CoreStore(restored)
    assert (
        export_core(public)["history"][0]["decision"]["hypothesis"]["claim"]
        == revision.decision.hypothesis.claim
    )
    assert public.events() == [] and public.current() == {}
    assert not public.private_state_available()
    candidate(public, text="NEW_PARTIAL_PRIVATE_STATE", suffix="-partial")
    save_public_projection(public)
    assert not public.private_state_available()
    assert public.public_current()[revision.hypothesis_id] == revision
    build_pages(public, restored / "site", repository=Path("."), synthetic=True)
    rendered = "\n".join(p.read_text(encoding="utf-8") for p in (restored / "site").glob("*.html"))
    assert revision.decision.hypothesis.claim in rendered
    assert "PRIVATE_UNCONFIRMED_BODY" not in rendered
    assert "用户已确认此正文与证据版本" in rendered
    assert "待人工确认 · 不会自动公开" not in rendered
    assert "PRIVATE_CONFIRMATION_NOTE" not in rendered
    assert "synthetic-owner" not in rendered


@pytest.mark.parametrize("damage", ["missing_prefix", "changed_prefix", "gap", "legacy"])
def test_private_restore_requires_complete_prefix(tmp_path, damage):
    store = CoreStore(tmp_path / "source")
    row = candidate(store)
    revision = store.current()[row["revision"]["hypothesis_id"]]
    decide(store, revision)
    projection = save_public_projection(store)
    dest = CoreStore(tmp_path / "restored")
    target = dest.root / projection.relative_to(store.root)
    target.parent.mkdir(parents=True)
    target.write_bytes(projection.read_bytes())
    shutil.copytree(store.directory, dest.directory)
    assert dest.private_state_available()
    # New legitimate events after the verified checkpoint are allowed.
    decide(dest, revision, action="return", operation="later-return")
    assert dest.private_state_available()
    files = sorted((dest.directory / "transactions").glob("*.json"))
    if damage == "missing_prefix":
        # Move only synthetic fixture data aside: retain the last committed event.
        files[0].rename(files[0].with_suffix(".excluded"))
    elif damage == "changed_prefix":
        value = json.loads(files[0].read_text(encoding="utf-8"))
        value["operation_id"] = "tampered-prefix"
        files[0].write_text(json.dumps(value), encoding="utf-8")
    elif damage == "gap":
        value = json.loads(files[-1].read_text(encoding="utf-8"))
        value["sequence"] += 1
        files[-1].write_text(json.dumps(value), encoding="utf-8")
    else:
        value = json.loads(target.read_text(encoding="utf-8"))
        value["private_state_checkpoint"] = store.event_digest(store.events()[-1])
        target.write_text(json.dumps(value), encoding="utf-8")
    original = target.read_bytes()
    assert not dest.private_state_available()
    # A partial return must not hide the confirmed public snapshot.
    assert dest.public_current()[revision.hypothesis_id] == revision
    save_public_projection(dest)
    assert target.read_bytes() == original


@pytest.mark.parametrize("summary", [
    "GPT-6.1 is faster than GPT-6.",
    "Compared with GPT-6, GPT-6.1 is faster.",
])
def test_version_comparison_is_not_a_conflict(summary):
    source = item("comparison", "GPT-6.1 released", "https://example.org/compare",
                  source="Author")
    source.summary = summary
    assert not input_version_conflicts([source])
    provider = FakeAIProvider()
    assert build_story([source], provider=provider, now=NOW).facts
    assert build_stories([source], provider=provider, now=NOW)[0].facts


def test_return_removes_public_projection_without_removing_local_history(tmp_path):
    store = CoreStore(tmp_path)
    row = candidate(store)
    revision = store.current()[row["revision"]["hypothesis_id"]]
    decide(store, revision)
    save_public_projection(store)
    decide(store, revision, action="return", operation="synthetic-return")
    save_public_projection(store)
    assert store.draft_revisions() and not export_core(store)["history"]


def test_already_tracked_private_state_blocks_before_add(tmp_path, monkeypatch):
    calls = []

    def git(root, *args):
        calls.append(args)
        return "data/tendency-core/v2/transactions/private.json" if args[0] == "ls-files" else ""

    monkeypatch.setattr(archive, "git", git)
    with pytest.raises(archive.ArchiveConflict, match="already tracked"):
        archive.GitArchive(tmp_path)("private")
    assert not any(args[0] in {"add", "commit", "push"} for args in calls)


def test_optional_quote_failure_preserves_base_facts_and_exact_rejection():
    base = item(
        "source", "Synthetic technical report", "https://example.org/source", source="Author"
    )
    draft = FakeAIProvider().merge_story([base]).model_dump(mode="json")
    point = {"text": "测试方法", "source_url": base.url, "quote": "a" * 281}
    draft["technical_reading"] = {"kind": "technical_article", "limitations": point}
    provider = SimpleNamespace()
    result = prepare_structured_output(
        provider, MergedStoryDraft, json.dumps(draft), task="merge_story"
    )
    checked = MergedStoryDraft.model_validate(result)
    assert checked.facts == draft["facts"] and checked.technical_reading.limitations is None
    assert provider.last_output_adaptation["rejected_fields"][0]["value"]["quote"] == "a" * 281
    assert json.loads(provider.last_output_adaptation["raw_response"]) == draft


def test_editorial_invalid_support_does_not_drop_valid_sibling():
    stories = [
        build_story(
            [item(x, x, "https://example.org/" + x, source=x)], provider=FakeAIProvider(), now=NOW
        )
        for x in ["a", "b", "c"]
    ]
    ids = [s.id for s in stories]

    def row(identity, place, **kw):
        return dict(
            story_id=identity,
            placement=place,
            reader_value=3,
            evidence_value=0,
            fact_status="claim",
            retain_for_trends=False,
            reason="合成选择",
            **kw,
        )

    raw = {
        "decisions": [
            row(ids[0], "TOP"),
            row(ids[1], "SUPPORT"),
            row(ids[2], "SUPPORT", support_for_story_id=ids[1]),
        ]
    }
    p = SimpleNamespace()
    data = prepare_structured_output(
        p, EditorialDecisionBatch, json.dumps(raw), task="evaluate_editorial"
    )
    result = validate_editorial_batch(EditorialDecisionBatch.model_validate(data), stories)
    assert [d.placement.value for d in result.decisions] == ["TOP", "ONE-LINER", "ONE-LINER"]
    assert p.last_output_adaptation["rejected_fields"]


def test_related_source_negative_classification_enters_bounded_merge():
    sources = [
        item("a", "Acme releases Nova Spark", "https://example.org/a", source="Media"),
        item("b", "Nova Spark", "https://example.org/b", source="Primary"),
    ]
    sources[0].summary = "Acme releases Nova Spark for developers."
    outputs = FakeAIProvider().classify_items(sources).model_dump(mode="json")
    outputs["items"][0]["relevant"] = True
    outputs["items"][1]["relevant"] = False

    class Capture(FakeAIProvider):
        seen = []

        def merge_story(self, items):
            self.seen.append(merge_story_payload(items))
            result = super().merge_story(items)
            return result.model_copy(update={"same_event": True})

    p = Capture()
    stories = build_stories(
        sources, provider=p, now=NOW, classifications=ClassificationBatch.model_validate(outputs)
    )
    assert len(stories) == 1 and len(p.seen[0]) == 2
    assert p.seen[0][1]["allowed_source_urls"] == [sources[1].url]
    correction = sources[1].model_copy(update={"id": "c", "title": "Nova Spark correction"})
    assert len(bounded_event_groups([sources[0], correction])) == 2


def test_bad_link_retains_only_original_lead_and_failure_status():
    source = item(
        "opaque-id", "A real source title", "https://example.org/original", source="Media"
    )

    class Bad(FakeAIProvider):
        calls = 0

        def merge_story(self, items):
            self.calls += 1
            raise AIOutputError("URL outside verified source set")

    p = Bad()
    outcomes = {}
    stories = build_stories([source], provider=p, now=NOW, item_outcomes=outcomes)
    assert p.calls == 1 and not stories[0].facts and stories[0].source_urls == [source.url]
    assert outcomes[source.id] == "merge_failed" and source.title in stories[0].canonical_title


def test_version_conflict_stays_a_lead_before_scoring():
    source = item(
        "conflict", "Task benchmark with Model-6.1", "https://example.org/conflict", source="Author"
    )
    source.summary = "Task benchmark with Model-6 delivers reported cost improvements."

    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError(name)

    result = build_story([source], provider=NoCalls(), now=NOW)
    assert not result.facts and "Model-6.1" in result.uncertainties[0]
    assert "Model-6 " in result.uncertainties[0]


def test_archived_card_reuse_requires_identical_context(tmp_path):
    story = build_story(
        [item("a", "Synthetic title", "https://example.org/a", source="Author")],
        provider=FakeAIProvider(),
        now=NOW,
    )
    brief = generate_daily_brief(
        brief_date=NOW.date(),
        generated_at=NOW,
        timezone="UTC",
        stories=[story],
        signals=[],
        provider=FakeAIProvider(),
        limits=BriefLimits(maximum_items=4),
        enabled_sections={k: True for k in SECTION_NAMES},
        run_stats={},
    )
    path = tmp_path / "brief.json"
    path.write_text(brief.model_dump_json(), encoding="utf-8")
    assert len(reuse_archived_cards([story], [path])) == 1
    indexed = story.model_copy(update={"evidence_ids": ["index-only"]})
    assert len(reuse_archived_cards([indexed], [path])) == 1
    assert not reuse_archived_cards([story.model_copy(update={"facts": ["不同事实"]})], [path])


def test_public_projection_cannot_resume_private_core(tmp_path):
    from morning_radar.tendencies.core import run_core

    path = tmp_path / "data/tendency-public/v1/confirmed.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"private_state_requires_restore": True, "history": [], "confirmations": []}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Private state restore"):
        run_core(tmp_path, config_root=Path("."), provider=FakeAIProvider(), now=NOW)


def test_candidate_review_selects_low_ranked_related_source_before_deep_processing():
    from morning_radar.intake.review import review_candidates
    from morning_radar.settings import AppConfig, load_model

    sources = [
        item("media", "Acme releases Nova Spark", "https://example.org/media", source="Media"),
        item("primary", "Nova Spark", "https://example.org/primary", source="Primary"),
        item("other", "Unrelated report", "https://example.org/other", source="Other"),
    ]
    sources[0].summary = "Acme releases Nova Spark for developers."
    records = [
        SimpleNamespace(
            item=i, input_id=i.id, content_version="v1", durable_at=NOW, first_seen_at=NOW
        )
        for i in sources
    ]
    selection = SimpleNamespace(
        records=records[:1], deferred=records[1:], items=sources[:1], selected_keys=set()
    )
    updates = {}

    class Ledger:
        def update(self, identity, version, **kwargs):
            updates[identity] = kwargs

        def save(self):
            pass

    class Reviewer(FakeAIProvider):
        def classify_items(self, items):
            result = super().classify_items(items)
            for row in result.items:
                row.relevant = row.important = row.item_id == "media"
            return result

    app = load_model(Path("config/app.yaml"), AppConfig).model_copy(
        update={"maximum_ai_items": 3, "maximum_ai_calls": 10, "candidate_review_limit": 3}
    )
    review_candidates(
        SimpleNamespace(selection=selection, ledger=Ledger(), process_now=NOW),
        provider=Reviewer(),
        app=app,
    )
    assert {i.id for i in selection.items} == {"media", "primary"}
    assert updates["primary"]["candidate_diagnostics"]["selected"]
