"""Bounded deterministic HN Algolia discovery for configured labs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from morning_radar.collectors.hn_common import clean_hn_text, hn_item, merge_hn_observations
from morning_radar.collectors.http import HttpClient, RequestBudgetExceeded
from morning_radar.models import RawItem


class HNSearchCollector:
    name = "hn_lab_search"
    endpoint = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(
        self,
        *,
        http: HttpClient,
        watchlist,
        now: datetime,
        window_hours: int = 30,
    ) -> None:
        self.http, self.watchlist, self.now = http, watchlist, now
        self.window_hours = window_hours
        self.discovery_audit = []

    def collect(self) -> list[RawItem]:
        if not self.watchlist.enabled:
            self.discovery_audit.append({"status": "disabled"})
            return []
        start = int((self.now - timedelta(hours=self.window_hours)).astimezone(UTC).timestamp())
        end = int(self.now.astimezone(UTC).timestamp())
        selected: dict[str, RawItem] = {}
        states = [
            {"lab": lab, "query": query, "status": "empty", "pages": 0,
             "requests": 0, "hits": 0, "accepted": 0, "rejected": 0, "more": False}
            for lab, query in self._query_schedule()
        ]
        for page in range(self.watchlist.maximum_pages_per_query):
            for state in states:
                if page and not state["more"]:
                    continue
                lab, query = state["lab"], state["query"]
                params = {
                        "query": query,
                        "tags": "story",
                        "restrictSearchableAttributes": "title",
                        "numericFilters": f"created_at_i>={start},created_at_i<={end}",
                        "hitsPerPage": self.watchlist.hits_per_page,
                        "page": page,
                }
                before = self.http.request_attempts
                try:
                    response = self.http.get(self.endpoint, params=params)
                    maximum_bytes = getattr(self.watchlist, "maximum_response_bytes", 262144)
                    if len(response.content) > maximum_bytes:
                        state["status"] = "partial" if state["pages"] else "failed"
                        state["reason"] = "response_too_large"
                        continue
                    payload = response.json()
                except RequestBudgetExceeded as exc:
                    state["status"] = "partial" if state["pages"] else "not_attempted_budget"
                    state["reason"] = "deadline" if "deadline" in str(exc) else "request_budget"
                    continue
                except Exception as exc:
                    state["status"] = "partial" if state["pages"] else "failed"
                    state["reason"] = type(exc).__name__
                    continue
                finally:
                    state["requests"] += self.http.request_attempts - before
                if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
                    state["status"], state["reason"] = "partial", "invalid_response"
                    continue
                state["pages"] += 1
                hits = payload["hits"]
                state["hits"] += len(hits)
                for hit in hits:
                    try:
                        item = self._convert(hit, lab.id, lab.aliases, query, start, end)
                    except (TypeError, ValueError):
                        item = None
                    if item is None:
                        state["rejected"] += 1
                        continue
                    state["accepted"] += 1
                    if item.id in selected:
                        selected[item.id] = merge_hn_observations([selected[item.id], item])[0]
                    elif item.id not in selected:
                        selected[item.id] = item
                if hits:
                    state["status"] = "ok"
                try:
                    state["more"] = page + 1 < int(payload.get("nbPages"))
                except (TypeError, ValueError):
                    state["status"] = "partial"
                    state["reason"] = "invalid_pagination"
                    state["more"] = False
                if state["more"] and page + 1 == self.watchlist.maximum_pages_per_query:
                    state["status"], state["reason"] = "partial", "page_limit"
        for state in states:
            self.discovery_audit.append(
                {"lab_id": state["lab"].id, "query": state["query"], "status": state["status"],
                 "pages": state["pages"], "requests": state["requests"], "hits": state["hits"],
                 "accepted": state["accepted"], "rejected": state["rejected"],
                 "persisted": 0, "reason": state.get("reason"), "window": [start, end]}
            )
        return list(selected.values())

    def _query_schedule(self):
        """Run every lab's primary query before spending budget on aliases."""
        labs = list(self.watchlist.labs)
        longest = max((len(lab.hn_queries) for lab in labs), default=0)
        scheduled = []
        for query_index in range(longest):
            for lab in labs:
                if query_index < len(lab.hn_queries):
                    scheduled.append((lab, lab.hn_queries[query_index]))
        return scheduled[: self.watchlist.maximum_queries_per_run]

    def _convert(self, hit, lab_id, aliases, query, start, end):
        if not isinstance(hit, dict):
            return None
        object_id, title, created = (
            str(hit.get("objectID") or ""),
            clean_hn_text(hit.get("title"), maximum_characters=500),
            hit.get("created_at_i"),
        )
        if (
            not object_id.isdigit()
            or not title
            or not isinstance(created, int)
            or not start <= created <= end
            or not any(alias.casefold() in title.casefold() for alias in aliases)
        ):
            return None
        return hn_item(
            story_id=int(object_id), title=title, original_url=hit.get("url"),
            text=hit.get("story_text"), author=hit.get("author"), submitted_at=created,
            fetched_at=self.now,
            maximum_excerpt_characters=self.watchlist.maximum_excerpt_characters,
            metadata={
                "discovery_paths": [f"hn_search:{lab_id}:{query}"], "lab_id": lab_id,
                "hn_submission_time": created, "selection_reason": "watchlist_discovery",
            },
        )
