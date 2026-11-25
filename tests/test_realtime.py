"""Websocket resilience tests against a fake Clipsal Cortex realtime server."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.schneider_se_monitor import api

from .conftest import make_jwt

pytestmark = pytest.mark.usefixtures("socket_enabled")

FRAME = {"type": "realtime_update", "payload": {"epoch": 1, "consumption": 500.0, "devices": []}}


class FakeAuth:
    """Stand-in for SEMonitorAuth."""

    def __init__(self, exp_in: float = 3600) -> None:
        self.exp_in = exp_in
        self.invalidated = 0
        self.tokens = 0

    async def async_get_id_token(self) -> str:
        self.tokens += 1
        return make_jwt(time.time() + self.exp_in)

    def invalidate(self) -> None:
        self.invalidated += 1


async def _serve(behaviours):
    """Start a server; behaviours[n] decides what connection n does."""
    state = {"n": 0, "times": []}

    async def handler(request: web.Request):
        n = state["n"]
        state["n"] += 1
        state["times"].append(time.monotonic())
        mode = behaviours[min(n, len(behaviours) - 1)]
        if mode == "401":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()  # subscribe
        await ws.send_json({"type": "info", "payload": {"message": "Subscribed"}})
        if mode == "online":
            for _ in range(3):
                await ws.send_json(FRAME)
                await asyncio.sleep(0.02)
            await ws.close()  # server drops a healthy session
        elif mode == "online_forever":

            async def pump():
                while not ws.closed:
                    await ws.send_json(FRAME)
                    await asyncio.sleep(0.02)

            sender = asyncio.create_task(pump())
            async for _msg in ws:  # answers the client's close frame
                pass
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        elif mode == "offline":
            async for _msg in ws:  # subscribed, but the monitor sends nothing
                pass
        elif mode == "chatty_offline":
            # monitor offline, but the server keeps sending status messages
            while not ws.closed:
                await ws.send_json({"type": "site_device_status", "payload": {"message": "status"}})
                try:
                    await ws.receive(timeout=0.05)
                except TimeoutError:
                    pass
        elif mode == "close":
            await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server, state


def _client(session, auth, server, received):
    rt = api.SEMonitorRealtime(session, auth, 1, on_data=received.append)
    rt.url = str(server.make_url("/"))
    rt.NO_DATA_TIMEOUT = 0.3
    rt.BACKOFF_MIN = 0.05
    rt.BACKOFF_MAX = 0.2
    return rt


async def _until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.01)


async def test_recovers_after_monitor_outage(caplog):
    """Online -> server drops -> monitor offline twice -> clean close -> back online."""
    server, state = await _serve(["online", "offline", "offline", "close", "online_forever"])
    received: list = []
    caplog.set_level(logging.INFO, logger="custom_components.schneider_se_monitor.api")
    async with aiohttp.ClientSession() as session:
        rt = _client(session, FakeAuth(), server, received)
        task = asyncio.create_task(rt.run())
        await _until(lambda: state["n"] >= 5 and rt.connected, timeout=10)
        frames_after_recovery = len(received)
        await _until(lambda: len(received) > frames_after_recovery + 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await server.close()

    t = state["times"]
    # healthy session that the server closed -> quick reconnect (min backoff)
    assert t[1] - t[0] < 0.3
    # silent/closing sessions back off progressively instead of hammering the server
    gaps = [t[i + 1] - t[i] for i in range(1, 4)]
    assert gaps[0] >= 0.3  # waited out NO_DATA_TIMEOUT before giving up on the silent session
    assert t[4] - t[3] >= 0.15  # clean close without data still backs off (not a 5 s->0 reset)
    assert rt.last_data is not None and rt.last_data.consumption == 500.0
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("unavailable" in m for m in msgs) == 1  # warned once per outage, not per retry
    assert sum("restored" in m for m in msgs) == 1


async def test_clean_close_without_data_does_not_tight_loop():
    server, state = await _serve(["close"])
    async with aiohttp.ClientSession() as session:
        rt = _client(session, FakeAuth(), server, [])
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(1.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await server.close()
    # backoff 0.05, 0.1, 0.2, 0.2, ... -> about 6-7 attempts in 1 s, not hundreds
    assert state["n"] <= 9


async def test_handshake_401_invalidates_token_then_recovers():
    server, state = await _serve(["401", "online_forever"])
    auth = FakeAuth()
    received: list = []
    async with aiohttp.ClientSession() as session:
        rt = _client(session, auth, server, received)
        task = asyncio.create_task(rt.run())
        await _until(lambda: len(received) >= 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await server.close()
    assert auth.invalidated == 1


# Cancelling mid-close leaves aiohttp's heartbeat timer to fire once on the closed socket.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_renews_token_while_data_is_flowing(expected_lingering_timers):
    """Token close to expiry: reconnect with a fresh token even though frames keep arriving."""
    server, state = await _serve(["online_forever"])
    auth = FakeAuth(exp_in=api.TOKEN_REFRESH_MARGIN + 0.3)
    async with aiohttp.ClientSession() as session:
        rt = _client(session, auth, server, [])
        task = asyncio.create_task(rt.run())
        await _until(lambda: state["n"] >= 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await server.close()
    assert auth.tokens >= 3
    assert state["times"][2] - state["times"][1] < 0.6  # renewal reconnects promptly, no backoff


async def test_login_revoked_stops_and_reports():
    class DeadAuth(FakeAuth):
        async def async_get_id_token(self):
            raise api.SEMonitorAuthError("Refresh Token has expired")

    errors: list = []
    async with aiohttp.ClientSession() as session:
        rt = api.SEMonitorRealtime(session, DeadAuth(), 1, on_data=lambda d: None, on_auth_error=errors.append)
        await asyncio.wait_for(rt.run(), 2)  # returns instead of retrying forever
    assert len(errors) == 1


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_status_messages_do_not_hide_an_outage(caplog, expected_lingering_timers):
    """Non-data messages must not keep a dead subscription alive."""
    server, state = await _serve(["chatty_offline", "online_forever"])
    received: list = []
    caplog.set_level(logging.INFO, logger="custom_components.schneider_se_monitor.api")
    async with aiohttp.ClientSession() as session:
        rt = _client(session, FakeAuth(), server, received)
        task = asyncio.create_task(rt.run())
        await _until(lambda: len(received) >= 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await server.close()
    assert state["n"] == 2
    assert state["times"][1] - state["times"][0] >= 0.3  # gave up after NO_DATA_TIMEOUT
    assert any("unavailable" in r.getMessage() for r in caplog.records)
