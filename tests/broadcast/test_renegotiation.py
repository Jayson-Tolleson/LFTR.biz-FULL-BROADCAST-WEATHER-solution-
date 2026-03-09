from __future__ import annotations

import asyncio

from server.state import AppState


def test_broadcaster_renegotiation_reuses_peer_for_same_sid(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="room")
        rtc = RTCManager(state)

        await rtc.start_broadcaster_from_offer("room", "b1", "offer-cam", "offer")
        first_pc = rtc.broadcasters["room"].pc

        # Simulate camera -> screen renegotiation by another offer from same broadcaster.
        await rtc.start_broadcaster_from_offer("room", "b1", "offer-screen", "offer")
        second_pc = rtc.broadcasters["room"].pc

        assert second_pc is first_pc

        # Simulate screen -> camera renegotiation.
        await rtc.start_broadcaster_from_offer("room", "b1", "offer-cam-back", "offer")
        third_pc = rtc.broadcasters["room"].pc

        assert third_pc is first_pc


    asyncio.run(_run())
def test_watcher_peer_survives_broadcaster_renegotiation(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="room")
        rtc = RTCManager(state)

        await rtc.start_broadcaster_from_offer("room", "b1", "offer-initial", "offer")
        rtc.broadcasters["room"].tracks["video"] = object()

        await rtc.start_viewer_offer("room", "w1")
        await rtc.set_viewer_answer("room", "w1", "answer-initial", "answer")
        watcher_pc = rtc.viewers["room"]["w1"]

        # Broadcaster renegotiates source changes; watcher should stay mapped/alive.
        await rtc.start_broadcaster_from_offer("room", "b1", "offer-switch-1", "offer")
        await rtc.start_broadcaster_from_offer("room", "b1", "offer-switch-2", "offer")

        assert rtc.viewers["room"]["w1"] is watcher_pc
        assert not watcher_pc.closed
    asyncio.run(_run())
