# Changelog

All notable changes to negmasd are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- **negmas now comes from PyPI (`>=0.16.0`)** instead of an editable `../negmas`
  path source. uv honours a git dependency's `[tool.uv.sources]`, so the path
  entry made `negmasd @ git+https://github.com/yasserfarouk/negmasd` fail to
  install in any other project (the path does not exist in the clone). Develop
  against a local negmas with `uv pip install -e ../negmas` after `uv sync`.
- **Python 3.14 is now the default development interpreter** (`.python-version`,
  now checked in rather than gitignored). `requires-python` stays `>=3.11`. The
  compiled dependencies that had no 3.14 wheels (numpy, scipy, pandas,
  scikit-learn, matplotlib, contourpy, kiwisolver, markupsafe, pydantic-core,
  pyyaml, rpds-py) were upgraded in `uv.lock`; numpy, scipy and contourpy now
  resolve to older releases on 3.11, which their newest releases no longer support.

### Documentation

- The **two execution modes** (`centralized` vs. `distributed`) are now the first
  thing the README and the docs home page explain, each illustrated with a diagram
  drawn to the same shape so the two real differences stand out: where each party's
  strategy + ufun live, and setup-only vs. per-round traffic. *Execution modes* also
  moved ahead of *Architecture* in the nav, and both pages carry the diagrams.

### Added

- **Pluggable transports** (`negmasd.transport`). Each role is now a plain
  `Service` that declares a **method table**; each transport is a projection of
  that table, so protocols cannot drift apart and negotiation logic never sees a
  protocol. Core pieces: `Address(host, port, protocol)`, `MethodSpec`/`Service`,
  and `Channel` (with both a blocking `call` and an awaitable `acall` — the
  handshake is async, but the per-round `respond` is driven from the synchronous
  mechanism loop). See the *Transports* page in the docs.
- **MQTT transport**, as an **optional** extra (`uv sync --extra mqtt`). All three
  roles serve over it with the same bit-exact `n_steps` parity as the other
  transports. It is broker-mediated, so `--broker host:port` (or
  `NEGMASD_MQTT_BROKER`) says where the broker is, while the topic is *derived*
  from each peer's `host`/`port` (`negmasd/<host>_<port>/rpc`) — which means **an
  existing HTTP config becomes an MQTT config by changing `protocol:` alone**, with
  no new config fields. Adding it changed zero lines in the HTTP and gRPC adapters;
  see the *Transports* page, which now documents the transport-independent design
  and illustrates it across all three protocols.
- **gRPC transport**, as an **optional** extra (`uv sync --extra grpc`). All
  three roles can serve over it, and outcomes are identical to HTTP. It uses a
  single generic `Call(method, payload_json)` RPC because the config models rely
  on pydantic `extra="allow"` for arbitrary negmas kwargs, which no fixed
  protobuf message can express, and because the wire must stay plain JSON of
  values for cross-version operation.
- **`--protocol http|grpc|mqtt`** on `negmasd runner`, `agent` and `ufun`;
  `--protocol http|grpc` on `builtin`. MQTT adds `--broker host:port`, and `--host`
  (new on `runner`) is that service's MQTT *identity* — the topic its peers address
  it by, never a bind address, so HTTP/gRPC still bind all interfaces.
- **`protocol` config fields**: `protocol` on each partner entry, plus `protocol`
  and `runner_protocol` on an agent config. All default to `http`, so nothing
  opts into gRPC implicitly. Protocols may be mixed per address.
- `negmasd builtin` prints a single machine-readable
  `builtin ready: {"protocol": …, "runner": …, "agents": {…}, "configs": …}` line,
  so callers can discover the ports actually in use.
- New test suites: `tests/test_grpc_parity.py` and `tests/test_mqtt_parity.py`
  (full-stack parity per transport, in-process and via real CLI subprocesses, plus
  the remote-ufun path), `tests/test_transport_optional.py` (the extra-absent
  guarantees for *both* optional transports, which run whether or not the extras
  are installed) and `tests/test_builtin_cli.py`. The two parity suites share
  `tests/transport_harness.py`, so the only difference between them is how services
  are started. `tests/conftest.py` starts a real MQTT broker in-process (`amqtt`,
  dev group), so the MQTT suite needs no mosquitto.

### Changed

- `negmasd builtin` **no longer fails when a preferred port is busy.** It picks a
  free port instead and rewrites the app's configs — each agent's own port, its
  runner, and every partner entry — so the pieces still find each other. It also
  waits for readiness instead of sleeping a fixed 3 seconds, removes the
  directory of rewritten configs on exit, and shuts its child servers down
  cleanly on `SIGTERM` instead of orphaning them.
  With `--no-load-configs` the configs cannot be rewritten, so port substitution
  is disabled there and a busy preferred port is reported as an error rather than
  silently leaving the agents addressing the wrong places.
- Role state moved from module-level globals onto service instances
  (`AgentService`, `RunnerService`, `UfunService`), which is what allows several
  services in one process. `negmasd.agent:app` and friends still exist, so
  `uvicorn negmasd.agent:app` is unaffected.
- Outbound calls go through a `Channel` (`common.call_service`;
  `common.get_response` remains for the URL-based form, and now honours the URL's
  scheme as the protocol).
- `SAORemoteNegotiator` and `RemoteUtilityFunction` accept an `address=`
  (and still a `base_url=` or a plain `transport=` callable).
- HTTP paths, verbs and response models are unchanged. `HttpChannel` now retries
  with the other verb on a `405`, making `http_method` a hint rather than
  something every caller must know.

- `negmasd builtin` rejects `--protocol mqtt` with a message naming the single-role
  commands to use instead: its whole job is allocating and substituting real ports,
  and MQTT has none.
- Readiness polling (the CLI's config auto-loader, and the test harness) now uses a
  short per-attempt timeout with retries instead of one long call. Over MQTT a
  QoS-1 publish to a topic nobody has subscribed to yet is silently discarded by the
  broker, so a single long attempt simply blocks where HTTP and gRPC would fail fast
  on connect.
- `Channel` now declares `timeout` as part of its contract (all three transports
  already had one). Callers legitimately tighten it: readiness polling needs a short
  timeout with retries, so two call sites no longer probe for the attribute
  defensively.
- The dev group gains `amqtt` (test broker only, never imported by negmasd). It
  pins `typer==0.27.1`, so the dev environment's typer moves up from 0.16; the suite
  was verified against it before any MQTT code was written.

### Fixed

- **Per-round reconnection.** `SAORemoteNegotiator` built a fresh `httpx.Client`
  — and so a fresh TCP connection — inside every round's call. Channels are now
  cached per address and hold one client for their lifetime. The same applied to
  `RemoteUtilityFunction` on every evaluation.
- **Silent error handling.** `common.get_response` caught `requests.exceptions.*`
  while making `httpx` calls, so only its generic fallback ever fired. Transport
  errors are now mapped in one place and reported with the failing call.
- **Partner `address:` vs `host:`.** `PartnerModel` only understands `host:`, so
  an `address:` key was silently dropped and the host quietly defaulted to
  `127.0.0.1`. `builtin` now normalises `address:` to `host:` when it rewrites
  configs. (Hand-written configs should still use `host:`.)
- A method whose HTTP path differs from its table name (the root health check)
  was reachable over gRPC but returned 404 over HTTP; `build_app` now publishes a
  `/<name>` alias. The mirror case — a path-spelled method such as `/` failing
  over gRPC — is fixed too.

## 0.1.0

Initial state: distributed and centralized execution over HTTP, with bit-exact
parity against in-process negmas proven for `n_steps`-bounded negotiations.
