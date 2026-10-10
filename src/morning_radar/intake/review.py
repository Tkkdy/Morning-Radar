"""Bounded metadata review ahead of deeper processing; raw remains authoritative."""

from __future__ import annotations

from collections import defaultdict

from morning_radar.ai import AIBudgetExceeded, AIOutputError
from morning_radar.ai.models import ClassificationBatch
from morning_radar.ai.request_payload import classify_items_payload, dumps
from morning_radar.intake.identity import intake_key
from morning_radar.intake.models import ProcessingStatus, ReasonCode


def review_candidates(prepared, *, provider, app) -> ClassificationBatch | None:
    original = prepared.selection
    pool = [*original.records, *original.deferred]
    unique = {intake_key(r.input_id, r.content_version): r for r in pool}
    if len(unique) <= len(original.records):
        return None
    # Interleave sources for review only; no source receives reserved display slots.
    queues = defaultdict(list)
    for record in unique.values():
        queues[record.item.source_name].append(record)
    for queue in queues.values():
        queue.sort(key=lambda r: r.item.published_at or r.item.fetched_at, reverse=True)
    ordered = []
    while any(queues.values()):
        for key in sorted(queues):
            if queues[key]:
                ordered.append(queues[key].pop(0))
    ordered = ordered[: app.candidate_review_limit]
    results = {}
    spent = 0
    for index in range(0, len(ordered), min(20, app.maximum_ai_items)):
        batch = ordered[index : index + min(20, app.maximum_ai_items)]
        compact = [
            r.item.model_copy(
                update={
                    "title": r.item.title[:160],
                    "summary": (r.item.summary or r.item.content_excerpt)[:120],
                    "content_excerpt": "",
                    "metadata": {"review_only": True},
                }
            )
            for r in batch
        ]
        size = len(dumps(classify_items_payload(compact, getattr(provider, "topic_context", None))))
        if spent + size > app.candidate_review_characters:
            break
        spent += size
        try:
            output = provider.classify_items(compact)
            ids = {r.item.id for r in batch}
            if len({item.item_id for item in output.items}) != len(output.items):
                raise AIOutputError("Duplicate review item")
            if any(item.item_id not in ids for item in output.items):
                raise AIOutputError("Unknown review item")
        except AIBudgetExceeded:
            break
        except AIOutputError:
            continue
        results.update({item.item_id: item for item in output.items})
    if not results:
        return None

    def rank(record):
        review = results.get(record.item.id)
        return (
            0
            if review and review.relevant and review.important
            else 1
            if review and review.relevant
            else 2
            if review is None
            else 3,
            record.durable_at or record.first_seen_at,
            record.input_id,
        )

    capacity = min(
        app.maximum_ai_items, app.maximum_raw_items,
        max(0, app.maximum_ai_calls - 7) * 2 // 5,
    )
    ranked = sorted(unique.values(), key=rank)
    from morning_radar.processing.deduplicate import _comparison_url
    grouped = {}
    for record in ranked:
        grouped.setdefault(_comparison_url(record.item), []).append(record)
    # One deep-processing opportunity per exact page; retain complementary aliases
    # for the existing Story merge path, bounded by the same item transport cap.
    selected = []
    selected_groups = 0
    for group in grouped.values():
        if selected_groups >= capacity:
            break
        if len(selected) + len(group) > app.maximum_ai_items:
            continue
        selected.extend(group)
        selected_groups += 1
    selected_keys = {intake_key(r.input_id, r.content_version) for r in selected}
    for record in unique.values():
        key = intake_key(record.input_id, record.content_version)
        review = results.get(record.item.id)
        chosen = key in selected_keys
        # Metadata review never terminally rejects raw evidence. A low-detail
        # relevance judgement is provisional and remains available for later runs.
        prepared.ledger.update(
            record.input_id,
            record.content_version,
            now=prepared.process_now,
            processing=ProcessingStatus.IN_PROGRESS if chosen else ProcessingStatus.DEFERRED_BUDGET,
            reason_code=None if chosen else ReasonCode.DEFERRED_BUDGET,
            stage="candidate_review",
            outcome="selected" if chosen else "awaiting_deep_review",
            candidate_diagnostics={
                "selected": chosen,
                "reviewed": review is not None,
                "important": review.important if review else None,
                "relevant": review.relevant if review else None,
                "reason": review.importance_reason
                if review
                else "review_budget_or_output_unavailable",
                "review_input_characters": spent,
            },
        )
        if chosen:
            from morning_radar.processing.filtering import filter_news_window

            if not filter_news_window(
                [record.item], now=prepared.process_now, hours=app.news_window_hours
            ):
                record.item.metadata["backfill_recorded_at"] = prepared.process_now.isoformat()
    original.records = selected
    original.items = [r.item for r in selected]
    original.selected_keys = selected_keys
    original.deferred = [r for key, r in unique.items() if key not in selected_keys]
    prepared.ledger.save()
    # Ranking from short metadata is provisional. Deep processing must classify
    # the full evidence, including negatively reviewed inputs when capacity remains.
    return None
