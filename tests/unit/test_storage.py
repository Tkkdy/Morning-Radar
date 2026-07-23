from datetime import UTC, datetime

from morning_radar.models import RawItem
from morning_radar.storage import load_model, load_models, read_json, save_model, save_models


def item(item_id: str) -> RawItem:
    return RawItem(
        id=item_id,
        title=f"Item {item_id}",
        url=f"https://example.com/{item_id}",
        source_name="Fixture",
        source_type="fixture",
        fetched_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


def test_save_and_load_model(tmp_path) -> None:
    path = tmp_path / "nested" / "item.json"

    save_model(path, item("one"))

    assert load_model(path, RawItem) == item("one")
    assert read_json(path)["fetched_at"].endswith("Z")


def test_save_and_load_model_list(tmp_path) -> None:
    path = tmp_path / "items.json"
    expected = [item("one"), item("two")]

    save_models(path, expected)

    assert load_models(path, RawItem) == expected
    assert not list(tmp_path.glob("*.tmp"))

