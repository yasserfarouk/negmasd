"""The MQTT transport: a third, broker-mediated protocol for every negmasd service.

Importing this module requires ``paho-mqtt`` (``uv sync --extra mqtt``). Nothing on
the HTTP path imports it — the registry in :mod:`negmasd.transport.base` holds only
a module-path *string* for this protocol, resolved the first time an MQTT peer is
actually addressed.

MQTT is pub/sub, not request/response, so two things differ from HTTP and gRPC:

**Addressing.** ``Address(host, port)`` keeps meaning *which peer*, exactly as for
the other transports; the MQTT topic is *derived* from it
(``negmasd/<host>_<port>/rpc``). Both ends compute that from config they already
share, so an existing HTTP config becomes an MQTT config by changing ``protocol:``
alone — no new fields. The broker is separate, process-level deployment
configuration (``--broker``, ``NEGMASD_MQTT_BROKER``), because a node talks to one
broker regardless of how many peers it addresses.

Note that under MQTT ``host``/``port`` are an *identifier*: nothing binds them. Two
services claiming the same ``host:port`` against one broker will collide, and unlike
a real port there is no "address already in use" to tell you. A serving process must
therefore be started with the ``--host``/``port`` its peers have configured for it;
:func:`serve_mqtt` logs the topic it listens on, and a client whose call times out
names the topic it published to, so the mismatch is easy to spot.

**Request/response.** Correlation rides *inside* the payload rather than using MQTT
5 response-topic properties, so this works against any broker on 3.1.1 or 5:

* request  → ``negmasd/<node>/rpc`` : ``{"id", "method", "payload", "reply_to"}``
* response → the request's ``reply_to``: ``{"id", "payload", "error"}``

Everything else mirrors :mod:`negmasd.transport.grpc`: a blocking ``call`` for the
synchronous per-round ``respond``, an ``acall`` that offloads it to a worker thread,
and a server that owns a thread with an asyncio loop because
``RunnerService.run_negotiation`` uses ``asyncio.create_task``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt

from .base import Address, Channel, Service, TransportError, to_jsonable

__all__ = [
    "MqttChannel",
    "MqttServerHandle",
    "serve_mqtt",
    "node_id",
    "request_topic",
    "get_broker",
    "set_broker",
    "close_broker_links",
    "BROKER_ENV_VAR",
    "DEFAULT_BROKER",
]

BROKER_ENV_VAR = "NEGMASD_MQTT_BROKER"
DEFAULT_BROKER = ("127.0.0.1", 1883)
TOPIC_ROOT = "negmasd"
QOS = 1

_broker_override: tuple[str, int] | None = None


# --------------------------------------------------------------------------- #
# broker configuration (process-level, not per-peer)
# --------------------------------------------------------------------------- #
def set_broker(host: str, port: int) -> None:
    """Point this process's MQTT traffic at ``host:port``."""
    global _broker_override
    _broker_override = (host, int(port))


def parse_broker(text: str) -> tuple[str, int]:
    """Parse a ``host:port`` (or bare ``host``) broker spec."""
    host, _, port = text.strip().rpartition(":")
    if not host:  # no colon: the whole string is a host
        return text.strip(), DEFAULT_BROKER[1]
    return host, int(port)


def get_broker() -> tuple[str, int]:
    """The broker to use: explicit override, then the env var, then the default."""
    if _broker_override is not None:
        return _broker_override
    env = os.environ.get(BROKER_ENV_VAR)
    return parse_broker(env) if env else DEFAULT_BROKER


# --------------------------------------------------------------------------- #
# topics
# --------------------------------------------------------------------------- #
def node_id(host: str | Address, port: int | None = None) -> str:
    """The topic-safe identity of a peer, derived from its address."""
    if isinstance(host, Address):
        host, port = host.host, host.port
    return f"{host}_{port}"


def request_topic(node: str) -> str:
    return f"{TOPIC_ROOT}/{node}/rpc"


def _new_client(client_id: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.reconnect_delay_set(min_delay=1, max_delay=8)
    return client


# --------------------------------------------------------------------------- #
# client side
# --------------------------------------------------------------------------- #
class _BrokerLink:
    """One connection to a broker, shared by every channel in this process.

    ``channel_for`` caches a channel per *peer*, but all peers on one broker can
    ride a single connection, so links are reference-counted rather than owned by
    any one channel.
    """

    def __init__(self, broker: tuple[str, int], timeout: float = 60.0):
        self.broker = broker
        self.client_id = f"negmasd-{uuid4().hex[:12]}"
        self.reply_root = f"{TOPIC_ROOT}/_reply/{self.client_id}"
        self._timeout = timeout
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._refs = 0
        self._client = _new_client(self.client_id)
        self._client.on_message = self._on_message
        self._connected = threading.Event()
        self._client.on_connect = lambda *a, **k: self._connected.set()
        try:
            self._client.connect(broker[0], broker[1], keepalive=60)
        except OSError as e:
            raise TransportError(
                f"cannot reach the MQTT broker at {broker[0]}:{broker[1]}: {e}."
                f" Set it with --broker or {BROKER_ENV_VAR}."
            ) from e
        self._client.loop_start()
        # Subscribing once to a wildcard beats one subscribe per call.
        self._client.subscribe(f"{self.reply_root}/+", qos=QOS)

    # -- lifecycle ----------------------------------------------------------- #
    def acquire(self) -> "_BrokerLink":
        with self._lock:
            self._refs += 1
        return self

    def release(self) -> bool:
        """Drop one user; returns True when the link was actually closed."""
        with self._lock:
            self._refs -= 1
            if self._refs > 0:
                return False
        self.close()
        return True

    def close(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- request/response ---------------------------------------------------- #
    def _on_message(self, client, userdata, message) -> None:  # noqa: ARG002
        try:
            body = json.loads(message.payload)
        except ValueError:
            return
        with self._lock:
            slot = self._pending.get(body.get("id", ""))
        if slot is None:  # a late reply to a call that already gave up
            return
        slot["reply"] = body
        slot["event"].set()

    def request(self, node: str, method: str, payload: dict[str, Any], timeout: float) -> Any:
        corr = uuid4().hex
        topic = request_topic(node)
        slot: dict[str, Any] = {"event": threading.Event(), "reply": None}
        with self._lock:
            self._pending[corr] = slot
        try:
            self._client.publish(
                topic,
                json.dumps(
                    {
                        "id": corr,
                        "method": method,
                        "payload": payload or {},
                        "reply_to": f"{self.reply_root}/{corr}",
                    }
                ),
                qos=QOS,
            )
            if not slot["event"].wait(timeout):
                raise TransportError(
                    f"no reply on {topic} within {timeout:g}s. Nothing is serving"
                    f" that topic: is the peer running with --protocol mqtt, the"
                    f" host/port its peers are configured with, and the same"
                    f" broker ({self.broker[0]}:{self.broker[1]})?"
                )
        finally:
            with self._lock:
                self._pending.pop(corr, None)
        reply = slot["reply"] or {}
        if reply.get("error"):
            raise TransportError(
                f"MQTT call {method!r} to {node} failed remotely: {reply['error']}"
            )
        return reply.get("payload")


_LINKS: dict[tuple[str, int], _BrokerLink] = {}
_LINKS_LOCK = threading.Lock()


def _link_for(broker: tuple[str, int], timeout: float) -> _BrokerLink:
    with _LINKS_LOCK:
        link = _LINKS.get(broker)
        if link is None:
            link = _BrokerLink(broker, timeout=timeout)
            _LINKS[broker] = link
        return link.acquire()


def close_broker_links() -> None:
    """Close every shared broker connection (used by tests and shutdown)."""
    with _LINKS_LOCK:
        links = list(_LINKS.values())
        _LINKS.clear()
    for link in links:
        link.close()


class MqttChannel(Channel):
    """A channel to one peer, over a broker."""

    def __init__(self, address: Address, timeout: float = 60.0):
        self.address = address
        self.node = node_id(address)
        self.timeout = timeout
        self.broker = get_broker()
        self._link: _BrokerLink | None = None

    def _get_link(self) -> _BrokerLink:
        if self._link is None:
            self._link = _link_for(self.broker, self.timeout)
        return self._link

    def close(self) -> None:
        link, self._link = self._link, None
        if link is not None:
            released = link.release()
            if released:
                with _LINKS_LOCK:
                    if _LINKS.get(self.broker) is link:
                        del _LINKS[self.broker]

    def call(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        # http_method is meaningless here, as for gRPC; accepted so that callers
        # stay protocol-agnostic.
        # Passed verbatim, as in the gRPC channel: the service's spec index holds
        # both spellings, so "respond", "/respond" and the root's "/" all resolve.
        return self._get_link().request(self.node, method, payload, self.timeout)

    async def acall(
        self, method: str, payload: dict[str, Any], *, http_method: str = "post"
    ) -> Any:
        return await asyncio.to_thread(
            self.call, method, payload, http_method=http_method
        )


# --------------------------------------------------------------------------- #
# server side
# --------------------------------------------------------------------------- #
class MqttServerHandle:
    """Serves one service on ``negmasd/<node>/rpc``.

    Owns a thread running an asyncio loop. That is required, not tidiness:
    ``RunnerService.run_negotiation`` calls ``asyncio.create_task``, and paho's
    callback thread has no event loop, so every message is handed off with
    ``run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        service: Service,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        node: str | None = None,
        broker: tuple[str, int] | None = None,
    ):
        self._service = service
        self.node = node or node_id(host, port)
        self.topic = request_topic(self.node)
        self.broker = broker or get_broker()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: mqtt.Client | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # -- dispatch ------------------------------------------------------------ #
    async def _handle(self, raw: bytes) -> None:
        client = self._client
        if client is None:  # pragma: no cover - shutting down
            return
        try:
            body = json.loads(raw)
        except ValueError:
            return
        reply_to = body.get("reply_to")
        if not reply_to:
            return
        out: dict[str, Any] = {"id": body.get("id", "")}
        try:
            result = await self._service.dispatch(
                body.get("method", ""), body.get("payload") or {}
            )
            out["payload"] = to_jsonable(result)
        except KeyError:
            out["error"] = f"unknown method {body.get('method')!r}"
        except Exception as e:  # surface as a reply, never a dropped request
            out["error"] = f"{type(e).__name__}: {e}"
        client.publish(reply_to, json.dumps(out), qos=QOS)

    def _on_message(self, client, userdata, message) -> None:  # noqa: ARG002
        loop = self._loop
        if loop is None:  # pragma: no cover - shutting down
            return
        asyncio.run_coroutine_threadsafe(self._handle(message.payload), loop)

    # -- lifecycle ----------------------------------------------------------- #
    def start(self, timeout: float = 30.0) -> str:
        """Connect, subscribe, and return the node id being served."""

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                client = _new_client(f"negmasd-srv-{uuid4().hex[:12]}")
                client.on_message = self._on_message
                subscribed = threading.Event()
                client.on_subscribe = lambda *a, **k: subscribed.set()
                client.connect(self.broker[0], self.broker[1], keepalive=60)
                client.loop_start()
                client.subscribe(self.topic, qos=QOS)
                if not subscribed.wait(timeout):
                    raise TransportError(f"could not subscribe to {self.topic}")
                self._client = client
            except BaseException as e:
                self._error = e
                self._ready.set()
                return
            self._ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="negmasd-mqtt")
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TransportError("MQTT server did not start in time")
        if self._error is not None:
            broker = f"{self.broker[0]}:{self.broker[1]}"
            raise TransportError(
                f"MQTT server failed to start against broker {broker}: {self._error}."
                f" Set the broker with --broker or {BROKER_ENV_VAR}."
            )
        return self.node

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def stop(self) -> None:
        loop, client = self._loop, self._client
        self._client = None
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # pragma: no cover - best effort
                pass
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = self._thread = None


def serve_mqtt(
    service: Service,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    node: str | None = None,
    broker: tuple[str, int] | None = None,
) -> None:
    """Block serving ``service`` over MQTT (the CLI's ``--protocol mqtt``)."""
    handle = MqttServerHandle(
        service, host=host, port=port, node=node, broker=broker
    )
    handle.start()
    print(
        f"negmasd MQTT server for {type(service).__name__} listening on"
        f" {handle.topic} via broker {handle.broker[0]}:{handle.broker[1]}"
    )
    try:
        handle.wait()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        handle.stop()
