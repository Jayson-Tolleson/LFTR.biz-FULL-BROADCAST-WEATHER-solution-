from __future__ import annotations

import asyncio

from server.state import AppState


def test_initial_broadcaster_watcher_handshake(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        ans = await rtc.start_broadcaster_from_offer("r1", "b1", "offer-sdp", "offer")
        assert ans["type"] == "answer"
        assert "r1" in rtc.broadcasters

        # Add broadcaster track to be relayed into watcher offer.
        rtc.broadcasters["r1"].tracks["video"] = object()

        offer = await rtc.start_viewer_offer("r1", "w1")
        assert offer["type"] == "offer"

        await rtc.set_viewer_answer("r1", "w1", "watch-answer", "answer")
        wpc = rtc.viewers["r1"]["w1"]
        assert wpc.signalingState == "stable"
        assert wpc.offer_count == 1


    asyncio.run(_run())
def test_glare_like_unstable_state_resets_broadcaster_peer(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer-1", "offer")
        first_pc = rtc.broadcasters["r1"].pc

        # Simulate glare/unstable state before next offer.
        first_pc.signalingState = "have-local-offer"

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer-2", "offer")
        second_pc = rtc.broadcasters["r1"].pc

        assert second_pc is not first_pc
        assert second_pc.signalingState in {"stable", "have-remote-offer"}


    asyncio.run(_run())
def test_cleanup_removes_broadcaster_and_viewers(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer", "offer")
        rtc.broadcasters["r1"].tracks["video"] = object()

        await rtc.start_viewer_offer("r1", "w1")
        await rtc.set_viewer_answer("r1", "w1", "watch-answer", "answer")
        await rtc.start_viewer_offer("r1", "w2")
        await rtc.set_viewer_answer("r1", "w2", "watch-answer-2", "answer")

        assert "r1" in rtc.broadcasters
        assert len(rtc.viewers.get("r1", {})) == 2

        await rtc.stop_broadcaster("r1", "b1")

        assert "r1" not in rtc.broadcasters
        assert rtc.viewers.get("r1", {}) == {}
    asyncio.run(_run())
