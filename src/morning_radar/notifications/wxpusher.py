"""WxPusher summary notification with daily idempotency."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from morning_radar.models import DailyBrief
from morning_radar.storage import read_json, write_json

LOGGER = logging.getLogger(__name__)
WXPUSHER_ENDPOINT = "https://wxpusher.zjiecode.com/api/send/message"


@dataclass(frozen=True, slots=True)
class WxPusherConfig:
    app_token: str
    uids: list[str]
    public_site_url: str

    @property
    def configured(self) -> bool:
        return bool(self.app_token and self.uids and self.public_site_url)


class WxPusherNotifier:
    def __init__(
        self,
        *,
        config: WxPusherConfig,
        state_path: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.state_path = state_path
        self.client = client or httpx.Client(timeout=20)

    def notify(self, brief: DailyBrief, *, force: bool = False) -> bool:
        if not self.config.configured:
            LOGGER.info("WxPusher configuration missing; notification skipped")
            return False
        state = read_json(self.state_path) if self.state_path.exists() else {}
        key = str(brief.date)
        if state.get(key) == "sent" and not force:
            LOGGER.info("WxPusher notification already sent for %s; skipped", key)
            return False
        titles = [item.title for item in brief.top_stories[:5]]
        overview = f"今日共收录 {sum(len(getattr(brief, name)) for name in _SECTIONS)} 条重点信号。"
        content = "\n".join(
            [
                *[f"{index}. {title}" for index, title in enumerate(titles, 1)],
                overview,
                f"完整晨报：{self.config.public_site_url.rstrip('/')}/",
            ]
        )
        response = self.client.post(
            WXPUSHER_ENDPOINT,
            json={
                "appToken": self.config.app_token,
                "content": content,
                "summary": f"Morning Radar｜{brief.date}",
                "contentType": 1,
                "uids": self.config.uids,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 1000:
            raise RuntimeError("WxPusher rejected the notification")
        state[key] = "sent"
        write_json(self.state_path, state)
        LOGGER.info("WxPusher notification sent for %s", key)
        return True

    def send_test(self) -> bool:
        if not self.config.configured:
            LOGGER.info("WxPusher configuration missing; test notification skipped")
            return False
        response = self.client.post(
            WXPUSHER_ENDPOINT,
            json={
                "appToken": self.config.app_token,
                "content": "Morning Radar 测试通知：配置可用。",
                "summary": "Morning Radar 测试",
                "contentType": 1,
                "uids": self.config.uids,
            },
        )
        response.raise_for_status()
        return response.json().get("code") == 1000


_SECTIONS = (
    "top_stories",
    "market_and_companies",
    "ai_and_open_source",
    "trend_radar",
    "developer_discussions",
)

