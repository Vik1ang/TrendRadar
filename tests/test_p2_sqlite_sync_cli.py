from __future__ import annotations

import json

import pytest

from scripts import p2_sqlite_sync


def test_history_discovery_mode_is_passed_into_container(monkeypatch) -> None:
    observed: dict[str, object] = {}
    response = {"type": "history_discovery", "version": 1, "databases": []}

    def fake_run(command, *, input_bytes=None, timeout=45, env=None):
        observed["command"] = command
        observed["input_bytes"] = input_bytes
        observed["timeout"] = timeout
        observed["env"] = env
        return json.dumps(response).encode("utf-8")

    monkeypatch.setattr(p2_sqlite_sync, "_run", fake_run)

    result = p2_sqlite_sync._history_discover()

    command = observed["command"]
    assert command[:5] == [
        "docker", "exec", "-i", "-e", "P2_HISTORY_EXPORT_MODE=discover",
    ]
    assert command[-3:] == [
        p2_sqlite_sync.TREND_RADAR_CONTAINER, "python", "-",
    ]
    assert observed["timeout"] == 90
    assert observed["env"] is None
    assert result == response


def test_history_discovery_passes_explicit_date_range(monkeypatch) -> None:
    observed: dict[str, object] = {}
    response = {"type": "history_discovery", "version": 1, "databases": []}

    def fake_run(command, *, input_bytes=None, timeout=45, env=None):
        observed["command"] = command
        return json.dumps(response).encode("utf-8")

    monkeypatch.setattr(p2_sqlite_sync, "_run", fake_run)
    assert p2_sqlite_sync._history_discover("2026-10-21", "2026-10-23") == response
    command = observed["command"]
    assert "P2_HISTORY_DATES=2026-10-21,2026-10-22,2026-10-23" in command


def test_history_range_is_bounded_and_validated() -> None:
    assert p2_sqlite_sync._history_dates("2026-10-21", "2026-10-23") == [
        "2026-10-21", "2026-10-22", "2026-10-23",
    ]
    for start, end in (
        ("2026-10-21", None),
        ("2026-10-24", "2026-10-23"),
        ("2026-02-30", "2026-03-01"),
        ("2026-01-01", "2026-02-01"),
    ):
        try:
            p2_sqlite_sync._history_dates(start, end)
        except ValueError:
            continue
        raise AssertionError(f"invalid range accepted: {start!r}..{end!r}")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--history-apply", "--max-total-articles", "100"], "requires --history-start-date and --history-end-date"),
        (["--history-apply", "--history-start-date", "2026-10-21", "--history-end-date", "2026-10-23"], "requires --max-total-articles"),
    ],
)
def test_history_apply_requires_date_bounds_and_total_limit(monkeypatch, capsys, arguments, message) -> None:
    monkeypatch.setattr("sys.argv", ["p2_sqlite_sync.py", *arguments])
    with pytest.raises(SystemExit) as error:
        p2_sqlite_sync.main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err
