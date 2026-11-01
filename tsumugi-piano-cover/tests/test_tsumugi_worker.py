from __future__ import annotations

from cover_studio.tsumugi_worker import default_window_batch_size


def test_window_batch_size_defaults_to_two(monkeypatch):
    monkeypatch.delenv("COVERSTUDIO_TSUMUGI_WINDOW_BATCH_SIZE", raising=False)
    assert default_window_batch_size() == 2


def test_window_batch_size_accepts_launcher_profiles(monkeypatch):
    for value in ("1", "2", "3", "4"):
        monkeypatch.setenv("COVERSTUDIO_TSUMUGI_WINDOW_BATCH_SIZE", value)
        assert default_window_batch_size() == int(value)


def test_invalid_window_batch_size_falls_back_safely(monkeypatch):
    for value in ("", "0", "5", "invalid"):
        monkeypatch.setenv("COVERSTUDIO_TSUMUGI_WINDOW_BATCH_SIZE", value)
        assert default_window_batch_size() == 1
