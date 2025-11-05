import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from one_shot.sensitivity import (
    detect_repo_sensitivity,
    ensure_task_sensitivity,
    is_sensitive_task,
    SensitivityLevel,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_detect_repo_sensitivity_public(monkeypatch):
    def fake_urlopen(request, timeout=10):
        assert request.full_url == "https://api.github.com/repos/acme/widget"
        return FakeResponse({"private": False})

    monkeypatch.setattr("one_shot.sensitivity.urlopen", fake_urlopen)
    level = detect_repo_sensitivity("https://github.com/acme/widget", token="ghp_dummy")
    assert level is SensitivityLevel.SAFE


def test_detect_repo_sensitivity_private(monkeypatch):
    def fake_urlopen(request, timeout=10):
        return FakeResponse({"private": True})

    monkeypatch.setattr("one_shot.sensitivity.urlopen", fake_urlopen)
    level = detect_repo_sensitivity("https://github.com/acme/secret", token="ghp_dummy")
    assert level is SensitivityLevel.SENSITIVE


def test_detect_repo_sensitivity_non_github():
    level = detect_repo_sensitivity("https://gitlab.com/acme/repo")
    assert level is SensitivityLevel.UNKNOWN


def test_ensure_task_sensitivity_marks_sensitive(monkeypatch):
    def fake_urlopen(request, timeout=10):
        return FakeResponse({"visibility": "private"})

    monkeypatch.setattr("one_shot.sensitivity.urlopen", fake_urlopen)

    tb_meta = {
        "metadata": {"tags": ["documentation"]},
        "repo": {"git_url": "https://github.com/acme/secret"},
    }
    level = ensure_task_sensitivity(tb_meta, token="ghp_dummy")
    assert level is SensitivityLevel.SENSITIVE
    assert tb_meta["sensitivity"]["level"] == "sensitive"
    assert is_sensitive_task(tb_meta)


def test_ensure_task_respects_existing_label(monkeypatch):
    tb_meta = {"sensitivity": {"level": "safe"}}

    # If detection were to run it would raise, so we ensure it is not called.
    monkeypatch.setattr("one_shot.sensitivity.urlopen", lambda *_, **__: pytest.fail("urlopen should not be called"))

    assert ensure_task_sensitivity(tb_meta) is SensitivityLevel.SAFE
    assert tb_meta["sensitivity"]["level"] == "safe"
