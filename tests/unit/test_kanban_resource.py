"""Kanban worker identity on the OTel Resource."""

from __future__ import annotations

KEYS = ("hermes.kanban.task_id", "hermes.kanban.board", "hermes.kanban.run_id")


class TestKanbanResourceAttributes:
    def test_present_in_worker_env(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc123")
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "alertzero")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "3")
        attrs = plugin._build_resource().attributes
        assert attrs["hermes.kanban.task_id"] == "t_abc123"
        assert attrs["hermes.kanban.board"] == "alertzero"
        assert attrs["hermes.kanban.run_id"] == "3"

    def test_absent_outside_a_worker(self, inmemory_otel_setup, monkeypatch):
        _, plugin = inmemory_otel_setup
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_RUN_ID", raising=False)
        attrs = plugin._build_resource().attributes
        assert not any(k in attrs for k in KEYS)

    def test_board_without_task_is_omitted(self, inmemory_otel_setup, monkeypatch):
        """Every ``hermes chat`` pins HERMES_KANBAN_BOARD at boot (hermes-agent
        #20074); only a dispatcher-spawned worker carries the task, so a board
        on its own must not tag an ordinary session."""
        _, plugin = inmemory_otel_setup
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "7")
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
        monkeypatch.delenv("HERMES_KANBAN_RUN_ID", raising=False)
        attrs = plugin._build_resource().attributes
        assert attrs["hermes.kanban.task_id"] == "t_only"
        assert "hermes.kanban.board" not in attrs
        assert "hermes.kanban.run_id" not in attrs
