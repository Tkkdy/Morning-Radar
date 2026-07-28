import logging
from datetime import UTC, date, datetime

import httpx
import pytest

from morning_radar.models import BriefItem, DailyBrief
from morning_radar.notifications import WxPusherConfig, WxPusherNotifier
from morning_radar.storage import read_json


def brief() -> DailyBrief:
    return DailyBrief(
        date=date(2026, 7, 23),
        timezone="Asia/Singapore",
        generated_at=datetime(2026, 7, 23, tzinfo=UTC),
        top_stories=[
            BriefItem(
                id="one",
                section="top_stories",
                title="Fixture title",
                what_happened="Fact",
                why_it_matters="Reason",
                source_urls=["https://example.com/source"],
                story_ids=["story-one"],
            )
        ],
    )


def test_missing_configuration_skips_without_state(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    notifier = WxPusherNotifier(
        config=WxPusherConfig("", [], ""),
        state_path=tmp_path / "state.json",
    )

    assert notifier.notify(brief()) is False
    assert not (tmp_path / "state.json").exists()
    assert "configuration missing" in caplog.text


def test_success_writes_state_and_duplicate_is_skipped_unless_forced(tmp_path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": 1000,
                "success": True,
                "msg": "ok",
                "data": [
                    {
                        "uid": "uid-1",
                        "code": 1000,
                        "status": "created",
                        "sendRecordId": 123,
                    }
                ],
            },
        )

    notifier = WxPusherNotifier(
        config=WxPusherConfig("secret-token", ["uid-1"], "https://radar.example"),
        state_path=tmp_path / "state.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert notifier.notify(brief()) is True
    assert notifier.notify(brief()) is False
    assert notifier.notify(brief(), force=True) is True
    assert len(calls) == 2
    assert read_json(tmp_path / "state.json")["2026-07-23"] == "sent"
    body = calls[0].read().decode()
    assert "Fixture title" in body
    assert "https://radar.example/" in body


def test_logs_do_not_include_token_or_uid(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    notifier = WxPusherNotifier(
        config=WxPusherConfig("super-secret-token", ["private-uid"], ""),
        state_path=tmp_path / "state.json",
    )

    notifier.notify(brief())

    assert "super-secret-token" not in caplog.text
    assert "private-uid" not in caplog.text


def test_failed_uid_task_is_not_marked_sent(tmp_path, caplog) -> None:
    caplog.set_level(logging.ERROR)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "code": 1000,
                "success": True,
                "data": [
                    {
                        "uid": "uid-ok",
                        "code": 1000,
                        "sendRecordId": 123,
                    },
                    {
                        "uid": "uid-failed",
                        "code": 1001,
                        "status": "rejected",
                    },
                ],
            },
        )

    notifier = WxPusherNotifier(
        config=WxPusherConfig(
            "secret-token",
            ["uid-ok", "uid-failed"],
            "https://radar.example",
        ),
        state_path=tmp_path / "state.json",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="task creation failed"):
        notifier.notify(brief())

    assert not (tmp_path / "state.json").exists()
    assert "failed to create 1 of 2 recipient task" in caplog.text
    assert "uid-failed" not in caplog.text
