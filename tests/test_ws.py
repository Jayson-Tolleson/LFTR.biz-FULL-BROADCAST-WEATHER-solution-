from server.rtc import state


def test_initial_watch_state_clean():
    assert state.broadcaster is None
    assert isinstance(state.viewers, set)
