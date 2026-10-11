"""Kanban worker identity on the OTel Resource."""

from __future__ import annotations

KEYS = ("hermes.kanban.task_id", "hermes.kanban.board")


class TestKanbanResourceAttributes:
    def test_present_in_worker_env(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc123")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "alertzero")
        attrs = plugin._build_resource().attributes
        assert attrs["hermes.kanban.task_id"] == "t_abc123"
        assert attrs["hermes.kanban.board"] == "alertzero"

    def test_absent_outside_a_worker(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
        attrs = plugin._build_resource().attributes
        assert not any(k in attrs for k in KEYS)

    def test_empty_values_are_omitted(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.setenv("HERMES_KANBAN_TASK", "  ")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "")
        attrs = plugin._build_resource().attributes
        assert not any(k in attrs for k in KEYS)

    def test_task_without_board(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_only")
        monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
        attrs = plugin._build_resource().attributes
        assert attrs["hermes.kanban.task_id"] == "t_only"
        assert "hermes.kanban.board" not in attrs
