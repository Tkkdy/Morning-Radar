from pathlib import Path

from morning_radar.processing.fixture_pipeline import process_fixture_file


def test_fixture_collection_and_grouping_are_fully_offline(monkeypatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixture pipeline must not access the network")

    monkeypatch.setattr("socket.create_connection", fail_network)
    fixture = Path("fixtures/sample_items.json")

    items, groups = process_fixture_file(fixture)

    assert len(items) == 4
    assert len(groups) == 3
    assert any(len(group) == 2 for group in groups)
    assert all(item.url.startswith("https://") for item in items)

