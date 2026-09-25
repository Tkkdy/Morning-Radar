import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from morning_radar.cli import main
from morning_radar.publishing.status import read_radar_status, write_radar_status


def test_daily_status_writer_preserves_dashboard_contract(tmp_path) -> None:
    path = tmp_path / "site/status.json"

    write_radar_status(
        path,
        run_date=date(2026, 9, 25),
        status="SUCCESS",
        updated_at=datetime(2026, 9, 24, 22, 45, tzinfo=UTC),
        detail="Morning brief generated",
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run_date": "2026-09-25",
        "status": "SUCCESS",
        "updated_at": "2026-09-24T22:45:00Z",
        "detail": "Morning brief generated",
    }


def test_post_daily_cli_reads_business_date_from_canonical_status(tmp_path, capsys) -> None:
    path = tmp_path / "status.json"
    write_radar_status(
        path,
        run_date=date(2026, 9, 25),
        status="SUCCESS",
        updated_at=datetime(2026, 9, 24, 22, 45, tzinfo=UTC),
        detail="Morning brief generated",
    )

    assert main(["read-status-date", "--path", str(path)]) == 0
    assert capsys.readouterr().out == "2026-09-25\n"


def test_daily_failure_cli_uses_the_same_canonical_schema(tmp_path) -> None:
    path = tmp_path / "status.json"

    assert (
        main(
            [
                "write-status",
                "--path",
                str(path),
                "--run-date",
                "2026-09-25",
                "--status",
                "FAILED",
                "--detail",
                "Daily Morning Radar workflow failed",
            ]
        )
        == 0
    )

    status = read_radar_status(path)
    assert status.run_date == date(2026, 9, 25)
    assert status.status == "FAILED"
    assert status.detail == "Daily Morning Radar workflow failed"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "date": "2026-09-25",
            "status": "SUCCESS",
            "updated_at": "2026-09-24T22:45:00Z",
            "detail": "Morning brief generated",
        },
        {
            "run_date": "2026-09-25",
            "status": "SUCCESS",
            "updated_at": "2026-09-24T22:45:00Z",
            "detail": "Morning brief generated",
            "unexpected": "schema drift",
        },
    ],
)
def test_status_schema_drift_fails_explicitly(tmp_path, payload) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        read_radar_status(path)
