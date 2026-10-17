"""#473: ``SessionState.first_root`` says whether this process has counted a session."""

from hermes_otel.session_state import PerSession, SessionState


class TestFirstRoot:
    def test_true_once_per_session_id(self):
        s = SessionState()
        assert s.first_root("s1") is True
        assert s.first_root("s1") is False
        assert s.first_root("s2") is True

    def test_empty_id_is_never_counted(self):
        assert SessionState().first_root("") is False

    def test_survives_the_per_turn_pop(self):
        """``on_session_end`` pops the PerSession each turn; the count must not reset."""
        s = SessionState()
        s.get_or_create("s1")
        assert s.first_root("s1") is True
        s.pop("s1")
        s.get_or_create("s1")
        assert s.first_root("s1") is False

    def test_drop_session_forgets_the_id(self):
        s = SessionState()
        assert s.first_root("s1") is True
        s.drop_session("s1")
        assert s.first_root("s1") is True

    def test_table_is_bounded(self):
        s = SessionState()
        for i in range(s._MAX_TURN_COUNTERS + 10):
            s.first_root(f"s{i}")
        assert len(s._counted) == s._MAX_TURN_COUNTERS


class TestApiFailureClose:
    """How a root that never got its on_session_end closes (#467, #509)."""

    def test_a_healthy_turn_closes_ok_with_the_caller_status(self):
        ps = PerSession()
        ps.record_api_failure("RateLimitError")
        ps.record_attempt(None, None, provider="", response_model="")  # the retry worked
        assert ps.api_failure_close("finalized") == (
            {"hermes.turn.final_status": "finalized"},
            "ok",
            None,
        )

    def test_a_failed_turn_closes_as_an_error_naming_the_type(self):
        ps = PerSession()
        ps.record_api_failure("APIConnectionError")
        attributes, status, message = ps.api_failure_close("timed_out")
        assert attributes == {
            "hermes.turn.final_status": "failed",
            "hermes.session.failed": True,
            "error.type": "APIConnectionError",
        }
        assert (status, message) == ("error", "turn failed: APIConnectionError")

    def test_a_failure_without_a_type_still_says_why(self):
        ps = PerSession()
        ps.record_api_failure("")
        attributes, status, message = ps.api_failure_close("timed_out")
        assert "error.type" not in attributes
        assert (status, message) == ("error", "turn failed: API request failed")
