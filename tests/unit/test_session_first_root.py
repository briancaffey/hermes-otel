"""#473: ``SessionState.first_root`` says whether this process has counted a session."""

from hermes_otel.session_state import SessionState


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
