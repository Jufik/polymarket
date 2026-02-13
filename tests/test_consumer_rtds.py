"""Tests for RTDS WebSocket consumer."""

import asyncio
import contextlib
import json

from polymarket_pipeline.consumers.rtds import RTDSConsumer


class FakeWebSocket:
    """Fake WS that yields canned messages then closes."""

    def __init__(self, messages: list[str]) -> None:
        self._msgs = messages
        self._idx = 0

    async def recv(self) -> str:
        if self._idx >= len(self._msgs):
            raise asyncio.CancelledError
        msg = self._msgs[self._idx]
        self._idx += 1
        return msg

    async def send(self, msg: str) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


SAMPLE_TRADE_MSG = json.dumps(
    {
        "connection_id": "test",
        "timestamp": 1770537659939,
        "topic": "activity",
        "type": "trades",
        "payload": {
            "asset": "12345",
            "conditionId": "0xtest",
            "side": "BUY",
            "price": 0.5,
            "size": 10.0,
            "timestamp": 1770537659,
            "transactionHash": "0xabc",
            "proxyWallet": "0xmaker",
            "outcome": "Yes",
            "outcomeIndex": 0,
            "name": "test",
            "pseudonym": "test",
            "bio": "",
            "profileImage": "",
            "icon": "",
            "title": "Test",
            "eventSlug": "test",
            "slug": "test",
        },
    }
)


async def test_consumer_processes_trade_messages() -> None:
    collected: list = []
    consumer = RTDSConsumer(on_trade=collected.append)

    ws = FakeWebSocket(["", SAMPLE_TRADE_MSG, "PING", SAMPLE_TRADE_MSG])
    with contextlib.suppress(asyncio.CancelledError):
        await consumer.consume(ws)

    assert len(collected) == 2
    assert collected[0].side.value == "BUY"
    assert collected[0].price.is_finite()


async def test_consumer_responds_to_ping() -> None:
    sent: list[str] = []

    class PingWS(FakeWebSocket):
        async def send(self, msg: str) -> None:
            sent.append(msg)

    ws = PingWS(["PING"])
    consumer = RTDSConsumer(on_trade=lambda t: None)
    with contextlib.suppress(asyncio.CancelledError):
        await consumer.consume(ws)

    assert "PONG" in sent
