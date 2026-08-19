# Changelog

All notable changes to negmasd are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- **Pluggable transports** (`negmasd.transport`). Each role is now a plain
  `Service` that declares a **method table**; each transport is a projection of
  that table, so protocols cannot drift apart and negotiation logic never sees a
  protocol. Core pieces: `Address(host, port, protocol)`, `MethodSpec`/`Service`,
  and `Channel` (with both a blocking `call` and an awaitable `acall` — the
  handshake is async, but the per-round `respond` is driven from the synchronous
  mechanism loop). See the *Transports* page in the docs.
- **gRPC transport**, as an **optional** extra (`uv sync --extra grpc`). All
  three roles can serve over it, and outcomes are identical to HTTP. It uses a
  single generic `Call(method, payload_json)` RPC because the config models rely
  on pydantic `extra="allow"` for arbitrary negmas kwargs, which no fixed
  protobuf message can express, and because the wire must stay plain JSON of
  values for cross-version operation.
- **`--protocol http|grpc`** on `negmasd runner`, `agent`, `ufun` and `builtin`.
- **`protocol` config fields**: `protocol` on each partner entry, plus `protocol`
  and `runner_protocol` on an agent config. All default to `http`, so nothing
  opts into gRPC implicitly. Protocols may be mixed per address.
- `negmasd builtin` prints a single machine-readable
  `builtin ready: {"protocol": …, "runner": …, "agents": {…}, "configs": …}` line,
  so callers can discover the ports actually in use.
- New test suites: `tests/test_grpc_parity.py` (full-stack gRPC parity,
  in-process and via real CLI subprocesses, plus the remote-ufun-over-gRPC path),
  `tests/test_transport_optional.py` (the gRPC-absent guarantees, which run
  whether or not the extra is installed) and `tests/test_builtin_cli.py`.

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
