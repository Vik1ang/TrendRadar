from __future__ import annotations

import json

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
