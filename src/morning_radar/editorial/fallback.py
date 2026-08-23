"""Safe editorial degradation without inventing missing decisions."""

from datetime import date, datetime

from morning_radar.editorial.models import DailyEditorialDecisions


def degraded_editorial_batch(
    *,
    current_date: date,
    generated_at: datetime,
    profile_version: str,
    shadow_mode: bool,
    reason: str,
) -> DailyEditorialDecisions:
    return DailyEditorialDecisions(
        date=current_date,
        generated_at=generated_at,
        profile_version=profile_version,
        enabled=True,
        shadow_mode=shadow_mode,
        degraded=True,
        degradation_reason=reason,
        decisions=[],
    )
