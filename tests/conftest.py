"""Fixtures shared by the transport suites."""

from __future__ import annotations

import asyncio
import logging
import socket
import threading

import pytest

from negmasd.transport import close_all_channels

# amqtt and its state-machine dependency are chatty at INFO/WARNING and none of it
# is actionable in a test run.
for _name in ("amqtt", "transitions", "amqtt.broker", "amqtt.mqtt.protocol.handler"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


@pytest.fixture
def clean_channels():
    """Channels are cached per address for the process lifetime; don't leak them."""
    close_all_channels()
    yield
    close_all_channels()


class _Broker:
    """A real MQTT broker in this process, so the MQTT suite needs no mosquitto."""

    def __init__(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.host = "127.0.0.1"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def start(self, timeout: float = 30.0) -> "_Broker":
        from amqtt.broker import Broker

        config = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self.host}:{self.port}",
                    "max_connections": 0,
                }
            },
            # amqtt >= 0.12: declaring `plugins` replaces entry-point discovery
            # (whose auth_file plugin rejects anonymous clients) and supersedes the
            # legacy `auth` / `topic-check` sections.
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {
                    "allow_anonymous": True
                }
            },
        }

        async def boot() -> None:
            # amqtt 0.12 captures the running loop in __init__, so it must be
            # constructed inside the loop, not merely started there.
            self._broker = Broker(config)
            await self._broker.start()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(boot())
            except BaseException as e:  # pragma: no cover - startup failure
                self._error = e
                self._ready.set()
                return
            self._ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="amqtt-broker")
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("test MQTT broker did not start in time")
        if self._error is not None:
            raise RuntimeError(f"test MQTT broker failed to start: {self._error}")
        return self

    def stop(self) -> None:
        loop, broker = self._loop, self._broker
        if loop is not None and broker is not None:
            try:
                asyncio.run_coroutine_threadsafe(broker.shutdown(), loop).result(
                    timeout=20
                )
            except Exception:  # pragma: no cover - best effort
                pass
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = self._broker = self._thread = None


@pytest.fixture
def mqtt_broker(clean_channels):
    """A running broker, with this process's MQTT traffic pointed at it."""
    pytest.importorskip("paho.mqtt.client", reason="needs the optional mqtt extra")
    pytest.importorskip("amqtt.broker", reason="needs amqtt (dev group) for a broker")
    from negmasd.transport.mqtt import close_broker_links, get_broker, set_broker

    previous = get_broker()
    broker = _Broker().start()
    set_broker(*broker.address)
    try:
        yield broker
    finally:
        close_all_channels()
        close_broker_links()
        set_broker(*previous)
        broker.stop()
