"""Small observer hooks (#400): commands, human input, skills, transcription."""

from __future__ import annotations

from _helpers import get_metric
from _helpers import metric_points as _metric_points
from _helpers import span_by_name as _span_by_name

from hermes_otel.hooks import (
    on_human_input_request,
    on_human_input_resolved,
    on_pre_command,
    on_pre_transcription,
    on_session_end,
    on_session_start,
    on_skill_lifecycle,
)


def _labels(reader, name):
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [dict(p.attributes) for p in _metric_points(get_metric(reader, name))]


def test_command_counter_never_reads_arguments(inmemory_otel_with_metrics):
    _, reader, _ = inmemory_otel_with_metrics
    assert (
        on_pre_command(command="model", surface="cli", args_raw="secret-arg", alias_used="m")
        is None
    )
    (labels,) = _labels(reader, "hermes.command.count")
    assert labels["command"] == "model" and labels["surface"] == "cli"
    assert "secret-arg" not in str(labels)


def test_clarify_wait_is_a_span_under_the_turn(inmemory_otel_with_metrics):
    exporter, reader, _ = inmemory_otel_with_metrics
    on_session_start(session_id="h", model="m", platform="cli")
    on_human_input_request(kind="clarify", request_id="r1", session_id="h", prompt="Which file?")
    on_human_input_resolved(kind="clarify", request_id="r1", session_id="h", outcome="answered")
    on_session_end(session_id="h", completed=True, interrupted=False, model="m", platform="cli")
    spans = exporter.get_finished_spans()
    wait = _span_by_name(spans, "human_input.clarify")
    root = _span_by_name(spans, "agent")
    assert wait.parent.span_id == root.context.span_id
    assert wait.attributes["hermes.human_input.outcome"] == "answered"
    assert wait.attributes["hermes.human_input.wait_s"] >= 0
    assert "Which file?" in wait.attributes["hermes.human_input.prompt"]
    assert {"kind": "clarify", "outcome": "answered"}.items() <= _labels(
        reader, "hermes.human_input.count"
    )[0].items()


def test_approval_wait_is_counted_not_spanned(inmemory_otel_with_metrics):
    exporter, reader, _ = inmemory_otel_with_metrics
    on_human_input_request(kind="approval", request_id="r2", session_id="", prompt="rm x")
    on_human_input_resolved(kind="approval", request_id="r2", outcome="approved")
    assert not [s for s in exporter.get_finished_spans() if s.name.startswith("human_input")]
    assert _labels(reader, "hermes.human_input.count")[0]["kind"] == "approval"


def test_skill_lifecycle_counts_action_not_name(inmemory_otel_with_metrics):
    _, reader, _ = inmemory_otel_with_metrics
    on_skill_lifecycle(action="patched", skill_name="my-private-skill", provenance="user")
    (labels,) = _labels(reader, "hermes.skill.lifecycle.count")
    assert labels["action"] == "patched" and labels["provenance"] == "user"
    assert "my-private-skill" not in str(labels)


def test_transcription_observer_returns_none_and_skips_the_file(inmemory_otel_with_metrics):
    _, reader, _ = inmemory_otel_with_metrics
    result = on_pre_transcription(
        file_path="/home/u/voice.ogg",
        provider="openai",
        model="whisper-1",
        language="en",
        prompt="p",
        source="telegram",
    )
    assert result is None  # a dict would rewrite Hermes's transcription request
    (labels,) = _labels(reader, "hermes.transcription.count")
    assert labels["provider"] == "openai" and labels["source"] == "telegram"
    assert "voice.ogg" not in str(labels)
