from __future__ import annotations

import asyncio

from server.state import AppState


def test_broadcaster_ice_is_queued_then_flushed(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        early = object()
        await rtc.add_broadcaster_ice_candidate("r1", "b1", early)
        key = rtc._broadcaster_ice_key("r1", "b1")
        assert len(rtc._pending_broadcaster_ice[key]) == 1

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer", "offer")

        bpc = rtc.broadcasters["r1"].pc
        assert key not in rtc._pending_broadcaster_ice
        assert early in bpc.added_candidates


    asyncio.run(_run())
def test_viewer_ice_queue_flush_after_answer(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer", "offer")
        rtc.broadcasters["r1"].tracks["video"] = object()

        await rtc.start_viewer_offer("r1", "w1")
        early = object()

        await rtc.add_viewer_ice_candidate("r1", "w1", early)
        key = rtc._viewer_ice_key("r1", "w1")
        assert len(rtc._pending_viewer_ice[key]) == 1

        await rtc.set_viewer_answer("r1", "w1", "answer", "answer")

        wpc = rtc.viewers["r1"]["w1"]
        assert key not in rtc._pending_viewer_ice
        assert early in wpc.added_candidates


    asyncio.run(_run())
def test_stale_broadcaster_queue_not_reused_across_sid_handoff(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        await rtc.add_broadcaster_ice_candidate("r1", "old_sid", object())
        assert rtc._pending_broadcaster_ice

        await rtc.start_broadcaster_from_offer("r1", "new_sid", "offer", "offer")

        for room_id, sid in rtc._pending_broadcaster_ice.keys():
            assert not (room_id == "r1" and sid == "old_sid")
    asyncio.run(_run())


def test_broadcaster_ice_deduplicates_while_pending(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)

        duplicate = object()
        await rtc.add_broadcaster_ice_candidate("r1", "b1", duplicate)
        await rtc.add_broadcaster_ice_candidate("r1", "b1", duplicate)
        key = rtc._broadcaster_ice_key("r1", "b1")
        assert len(rtc._pending_broadcaster_ice[key]) == 1

    asyncio.run(_run())


def test_broadcaster_ice_queue_cap_and_cleanup(rtc_patched):
    async def _run():
        from server.rtc import RTCManager

        state = AppState(default_room="r1")
        rtc = RTCManager(state)
        rtc.max_pending_ice = 3

        for i in range(8):
            await rtc.add_broadcaster_ice_candidate("r1", "b1", {"candidate": f"cand-{i}"})

        key = rtc._broadcaster_ice_key("r1", "b1")
        assert len(rtc._pending_broadcaster_ice[key]) == 3
        assert ("broadcaster", "r1", "b1") in rtc._ice_queue_started

        await rtc.start_broadcaster_from_offer("r1", "b1", "offer", "offer")
        assert key not in rtc._pending_broadcaster_ice
        assert key not in rtc._pending_broadcaster_ice_seen
        assert ("broadcaster", "r1", "b1") not in rtc._ice_queue_started

        await rtc.stop_broadcaster("r1", "b1")
        assert key not in rtc._pending_broadcaster_ice

    asyncio.run(_run())
