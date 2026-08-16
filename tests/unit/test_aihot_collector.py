from datetime import UTC, datetime
from types import SimpleNamespace

from morning_radar.collectors.aihot import AIHOTCollector
from morning_radar.collectors.orchestrator import collect_available
from morning_radar.models import RawItem, SourceRole, StatementType
from morning_radar.settings import AIHOTConfig

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


class StubHttp:
    def __init__(self, payload: dict, *, etag: str = '"v1"') -> None:
        self.payload = payload
        self.etag = etag
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs: object):
        self.calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            headers={"ETag": self.etag},
            url=f"{url}?mode=selected&window=24h&limit=8",
            json=lambda: self.payload,
        )


def item_payload() -> dict:
    return {
        "id": "public-1",
        "title": "A builder reports a concrete agent workflow change",
        "summary": "AIHOT discovery summary that must not become a Story fact by itself.",
        "source": {"name": "Original Builder Blog"},
        "links": {
            "aihot": "https://aihot.virxact.com/read/public-1",
            "original": "https://builder.example/posts/workflow-change",
        },
        "publishedAt": "2026-08-16T00:00:00Z",
        "discoveredAt": "2026-08-16T00:30:00Z",
        "selected": True,
        "reason": "Concrete practitioner workflow evidence.",
        "attribution": "AIHOT",
    }


def test_aihot_is_disabled_without_network_or_state_write(tmp_path) -> None:
    http = StubHttp({"items": [item_payload()]})
    state_path = tmp_path / "state.json"
    collector = AIHOTCollector(
        AIHOTConfig(enabled=False), http=http, state_path=state_path, now=NOW
    )

    assert collector.collect() == []
    assert http.calls == []
    assert not state_path.exists()


def test_aihot_v1_item_preserves_original_provenance_and_discovery_semantics(
    tmp_path,
) -> None:
    http = StubHttp({"schemaVersion": 1, "items": [item_payload()]})
    collector = AIHOTCollector(
        AIHOTConfig(enabled=True, limit=8),
        http=http,
        state_path=tmp_path / "state.json",
        now=NOW,
    )

    [item] = collector.collect()

    assert item.url == "https://builder.example/posts/workflow-change"
    assert item.source_name == "Original Builder Blog"
    assert item.source_role is SourceRole.UPSTREAM_DISCOVERY
    assert item.statement_type is StatementType.UNVERIFIED_LEAD
    assert item.metadata["aihot_public_id"] == "public-1"
    assert item.metadata["aihot_url"] == "https://aihot.virxact.com/read/public-1"
    assert item.metadata["discovery_only"] is True
    _, request = http.calls[0]
    assert request["params"] == {"mode": "selected", "window": "24h", "limit": 8}


def test_aihot_reuses_saved_etag(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    first = StubHttp({"items": [item_payload()]})
    AIHOTCollector(
        AIHOTConfig(enabled=True), http=first, state_path=state_path, now=NOW
    ).collect()
    second = StubHttp({"items": []})

    AIHOTCollector(
        AIHOTConfig(enabled=True), http=second, state_path=state_path, now=NOW
    ).collect()

    assert second.calls[0][1]["headers"] == {"If-None-Match": '"v1"'}


def test_aihot_failure_is_source_scoped_and_does_not_break_other_collection(
    tmp_path,
) -> None:
    broken = AIHOTCollector(
        AIHOTConfig(enabled=True),
        http=StubHttp({"unexpected": []}),
        state_path=tmp_path / "state.json",
        now=NOW,
    )
    ordinary_item = RawItem(
        id="ordinary",
        title="Ordinary verified source",
        url="https://example.com/ordinary",
        source_name="Ordinary",
        source_type="fixture",
        fetched_at=NOW,
    )
    ordinary = SimpleNamespace(name="ordinary", collect=lambda: [ordinary_item])

    result = collect_available([ordinary, broken], maximum_items=10)

    assert result.items == [ordinary_item]
    assert result.failures == {"aihot_discovery": "ValueError"}
