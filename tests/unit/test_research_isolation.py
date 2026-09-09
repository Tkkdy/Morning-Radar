from morning_radar.ai import FakeAIProvider
from morning_radar.ai.models import ResearchResolutionBatch
from morning_radar.models import SourceRole
from morning_radar.research.engine import resolve_research
from morning_radar.research.isolation import IsolatedResearchResult
from tests.unit.test_research import item


class MixedResearchProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0

    def resolve_research_cases_isolated(self, cases):
        self.calls += 1
        valid = []
        invalid = []
        missing = []
        for index, case in enumerate(cases):
            if index == 0:
                invalid.append(case.id)
                continue
            if index == 1 and len(cases) > 1:
                missing.append(case.id)
                continue
            valid.extend(super().resolve_research_cases([case]).cases)
        return IsolatedResearchResult(
            batch=ResearchResolutionBatch(cases=valid),
            invalid_ids=invalid,
            missing_ids=missing,
        )


class TruncatingProvider(FakeAIProvider):
    def __init__(self) -> None:
        self.calls = 0

    def resolve_research_cases_isolated(self, cases):
        self.calls += 1
        if len(cases) > 1:
            return IsolatedResearchResult(
                batch=ResearchResolutionBatch(),
                truncated=True,
            )
        return IsolatedResearchResult(batch=super().resolve_research_cases(cases))


def test_research_keeps_valid_cases_when_one_is_invalid_and_one_missing() -> None:
    lead = item("practitioner", role=SourceRole.PRACTITIONER)
    support = item("official", role=SourceRole.OFFICIAL_PRIMARY)
    provider = MixedResearchProvider()
    result = resolve_research(
        [lead, support],
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=0,
    )
    assert result.stats["research_invalid_cases"] >= 1
    assert provider.calls == 1


def test_truncated_batch_splits_instead_of_dropping_everything() -> None:
    first = item("p1", role=SourceRole.PRACTITIONER, url="https://example.com/one")
    second = item("p2", role=SourceRole.PRACTITIONER, url="https://example.com/two")
    provider = TruncatingProvider()
    result = resolve_research(
        [
            first,
            item("o1", role=SourceRole.OFFICIAL_PRIMARY, url=first.url),
            second,
            item("o2", role=SourceRole.OFFICIAL_PRIMARY, url=second.url),
        ],
        provider=provider,
        maximum_cases=8,
        maximum_radar_signals=3,
        item_retry_attempts=0,
        split_retry_attempts=2,
    )
    assert provider.calls >= 2
    assert result.cases
