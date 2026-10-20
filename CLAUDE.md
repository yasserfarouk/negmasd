# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`negmasd` distributes a [negmas](https://github.com/yasserfarouk/negmas) negotiation across processes/machines. Each role is a transport-agnostic service; HTTP (FastAPI) and gRPC are both supported. The goal is to run any negmas mechanism (primarily bilateral `SAOMechanism`) with each negotiator — and optionally each ufun — in its own process.

`negmas` is installed from PyPI. Do **not** add a `[tool.uv.sources]` path entry for it: uv honours a git dependency's sources, so a `../negmas` path breaks every project that installs negmasd from GitHub (the path does not exist in the clone). To develop against a local negmas checkout, run `uv pip install -e ../negmas` after `uv sync`.

## Commands

```bash
uv sync                              # install (creates .venv) -- HTTP only, no grpcio
uv sync --extra grpc                 # ...plus the optional gRPC transport
negmasd --help                       # CLI entry point (typer app)
negmasd builtin procurement          # start runner(9010) + buyer(8001) + seller(8002), auto-load configs
negmasd runner --port 9010           # start a single runner service
negmasd agent <port> --config <f>    # start a single agent service (auto-loads config when ready)
negmasd ufun <port> --config <f>     # start a utility-function service (for remote ufuns)
negmasd runner|agent|ufun --protocol grpc|mqtt   # mqtt also needs --broker H:P
uv sync --extra mqtt                 # optional MQTT transport (paho-mqtt)
.venv/bin/python -m pytest tests/    # run the test suite (pytest 9.x in the venv)
.venv/bin/python -m pytest tests/ -m "not slow"   # skip the subprocess full-stack tests
.venv/bin/python ...                 # invoke python directly; `python` is not on PATH
src/negmasd/transport/regen_proto.sh # regenerate the checked-in protobuf stubs (needs the dev group)
```

No linter or build step is configured. `docs/timing.md` is empty.

To exercise a negotiation: run `negmasd builtin procurement`, open `http://127.0.0.1:8001/docs`, call `/start-negotiation?neg=trade`, then `/peak` on any process to inspect results.

## Architecture

Three process roles. Each is a plain Python `Service` (no transport code) that
declares a **method table**; a transport adapter projects that table onto a wire
protocol. See "Transports" below.

- **Agent** (`agent.py`, `AgentService`) — one per negotiator. Holds that party's **private** info (its `negotiator` strategy + `ufun`) in an `AgentConfigModel`, keyed by negotiation name.
- **Runner** (`runner.py`, `RunnerService`) — single shared service that builds and runs the negmas `Mechanism`.
- **Ufun service** (`ufun_service.py`, `UfunService`) — optionally hosts a ufun in its own process.
- **CLI** (`cli.py`) — spawns processes and pushes configs via `/load-config`.

Each of those modules keeps a process-wide `service` instance and an `app = build_app(service)`, so `uvicorn negmasd.agent:app` still works. Service state lives on the instance (not module globals), which is what allows several services in one process — the gRPC tests rely on this.

**Handshake for one negotiation** (see `agent.start_negotiation` → `runner.run_negotiation` → `runner.async_run`):
1. Initiator agent mints a `negid`, strips private fields via `PersonalNegotiationModel.remove_personal_info()` → public `NegotiationModel`, and calls `accept-negotiation` on each partner.
2. On all-accept, it calls `run-negotiation` on the runner.
3. Runner calls `neg-strategy` on each partner to pull their private strategy+ufun, builds the mechanism, runs it, and sends `NegResultsModel` to each partner's `receive-results`.

(Those are method-table names. Over HTTP they are the same paths and verbs as before — `/accept-negotiation` POST, `/neg-strategy` GET, and so on.)

**Object construction from YAML** is the central pattern. `MakableModel.make_object` (`models.py`) feeds a model's dict — a `type:` field plus arbitrary extra fields (Config `extra="allow"`) — into negmas's `deserialize(..., python_class_identifier="type", remove_type_field=True)`. So any negmas class (mechanism, outcome space, negotiator, ufun) is specified declaratively in YAML by its dotted path under `type:` with constructor kwargs as siblings. Apps live in `apps/<name>/` as `runner.yml` + `agents/*.yml`.

Outbound calls go through a `Channel` (`common.call_service`, or `common.get_response` for the URL-based form). State is in-memory only (service instance attributes); no persistence.

## Execution modes (`NegotiationModel.mode`)

A negotiation runs in one of two modes; both reach **bit-exact parity** with negmas-proper (proven in `tests/`):

- **`distributed`** (default) — runner runs the real `SAOMechanism` but adds one `SAORemoteNegotiator` per partner (`negotiator.py`). The proxy holds **no ufun**; each round it sends the state to the partner's `respond`, where the agent runs its real, persistent negotiator (built in `/init-negotiation`, stored in `agent.live_negotiators`). This keeps strategy + ufun in the partner's own process.
- **`centralized`** — runner pulls each partner's strategy + ufun via `/neg-strategy` and runs the whole `SAOMechanism` locally. It *is* negmas in one process; parity is about config serialization fidelity.

A third level: a ufun can run in its own process. `RemoteUtilityFunction` (`ufuns.py`) is a real `BaseUtilityFunction` whose `eval` is forwarded to a `ufun_service.py` process — it's just another ufun `type` in config, so the agent builds it with no special-casing.

### Why exact parity is achievable (and the gotchas)

A time-based negotiator's response is a pure function of the *passed* `SAOState` plus its own ufun — not the mechanism's clock — so feeding the agent's real negotiator the exact per-round states reproduces negmas exactly. Two hard-won invariants make the wire (`wire.py`) correct, and **must not regress**:

1. **Never use `negmas.serialization.serialize` for runtime `SAOState`/`SAOResponse`.** It silently drops the `ResponseType` enum to `{}` (losing ACCEPT) — carry it as `int(response.value)`.
2. **Re-tuple every outcome on decode.** JSON has no tuples; an outcome left as a list makes `ufun(offer)` return `None`, so the negotiator silently rejects everything.

**RNG / determinism:** only the *runner's* `SAOMechanism` draws from the global RNG (e.g. random first-proposer). Agents running `stochastic=False` negotiators need no RNG sync — exact parity holds even with agent-side RNG poisoned. For reproducibility set `NegotiationModel.seed`; the runner does `random.seed(seed)` immediately before `m.run()`. This is also what makes the parity tests deterministic.

**Cross-version / cross-machine:** the wire is plain JSON of *values* (never pickle, never object refs). Each agent builds its own negotiator+ufun from its own config in its own env; the runner never deserializes an agent's objects. So agents may run different Python versions / machines.

### Distributed-mode boundary (proven envelope — don't overclaim past it)

Exact parity is proven for **step-bounded** (`n_steps`) negotiations with **`stochastic=False`, NMI-independent, time-based** negotiators (Boulware/Aspiration). Outside that envelope:

- **`time_limit` in distributed mode is supported and expected, but not bit-exact** with an in-process run: each round costs an HTTP round-trip, so the wall-clock `relative_time` the negotiator sees differs. This is fine by design — exact parity is only claimed for `n_steps`-bounded negotiations. Don't add a warning/error for it.
- **The agent's negotiator only ever receives `__call__(state)`** — it is never `join`ed to a mechanism and gets no `on_negotiation_start`/`on_round_start`/`on_negotiation_end` lifecycle callbacks, and no `self.nmi`. Strategies that read `self.nmi` or initialize in a lifecycle hook will not work distributed without extending the agent (build a local mirror mechanism to host the negotiator).
- **`wire.py` carries the SAO fields a time-based negotiator reads**, not the full `SAOState` (it drops `current_data`/`new_data`/`threads`/`results`). Protocols that pass data through those fields need the codec extended.

## Transports

`negmasd/transport/` separates *what* a service does from *how* bytes reach it.

- **`base.py`** — `Address(host, port, protocol)`; `MethodSpec` / `Service` (the method table); `Channel` (a connection to one peer, with **both** `call` (sync) and `acall` (async) — both are required: the handshake calls are awaited, but the per-round `respond` is made from the synchronous `Mechanism.run` loop); plus the protocol registries.
- **`http.py`** — `HttpChannel` (keeps one `httpx.Client` for the channel's lifetime, so the per-round call reuses a connection) and `build_app(service)`, which registers one FastAPI route per `MethodSpec`. HTTP paths and verbs are unchanged from when the endpoints were hand-decorated.
- **`mqtt.py`** — `MqttChannel` and `serve_mqtt`, over a broker. `Address(host, port)` still means *which peer*; the topic is derived (`negmasd/<host>_<port>/rpc`) so an existing HTTP config becomes MQTT by changing `protocol:` alone. The broker is process-level (`--broker`, `NEGMASD_MQTT_BROKER`). Correlation rides in the payload (not MQTT 5 response-topic), so any 3.1.1/5 broker works. Server owns a thread+loop for the same `create_task` reason as gRPC. **Two MQTT-specific traps:** `host:port` is an identifier that nothing binds, so a duplicate collides *silently*; and a publish to a topic with no subscriber yet is *discarded*, so readiness must be polled with short timeouts and retries rather than one long call.
- **`grpc.py`** — `GrpcChannel` and `serve_grpc`. A single generic `Call(method, payload_json) -> Reply(payload_json)` RPC (`negmasd.proto`), *not* typed messages per endpoint: the config models set pydantic `extra="allow"` to pass arbitrary negmas constructor kwargs into `make_object`, which no fixed proto can express, and a shared generated schema would break the cross-version guarantee described under "Why exact parity is achievable" above. The server is `grpc.aio` because `RunnerService.run_negotiation` uses `asyncio.create_task`.

Rules that keep this coherent — **don't regress these**:

1. **gRPC and MQTT are optional and must stay optional.** `grpcio` is a compiled wheel that fails to install on some Python builds; `paho-mqtt` is only wanted by MQTT deployments. Each lives behind its extra, and the registry stores only a *module-path string*, so nothing imports `grpc`/`paho` until a peer using that protocol is addressed. `tests/test_transport_optional.py` enforces this for both by blocking the import in a subprocess — it runs whether or not the extras are installed.
2. **A method's *name* must be addressable on every transport.** gRPC and MQTT dispatch by name, so `build_app` also publishes a `/<name>` alias when a spec overrides its HTTP `path` (only the root health check does, at `/`). Without it a caller reaches a method over gRPC but 404s over HTTP. Asserted by `test_http_and_grpc_expose_the_same_methods`.
3. **`respond` keeps `response_model=None`.** The wire-encoded `SAOState`/`SAOResponse` must pass through the table as a raw dict; a pydantic round-trip is exactly where `ResponseType` collapses to `{}` and outcome tuples degrade to lists (see the wire invariants above).
4. **`http_method` is a hint.** It is read only by the FastAPI adapter (gRPC and MQTT have no GET/POST distinction), and `HttpChannel` retries with the other verb on a 405, so protocol-agnostic call sites need not know HTTP verbs.

Channels are cached per `(protocol, host, port)` for the process lifetime (that cache *is* the connection reuse); `close_all_channels()` resets it, which is what the tests use between cases.

Adding a protocol means implementing a `Channel` plus a serve function and registering both — no negotiation logic changes. MQTT is the evidence: it changed **zero lines** in `http.py`/`grpc.py` and added no field to `Address` or any config model.

Tests for a new transport should reuse `tests/transport_harness.py` (scenario, configs, driving) so the only per-transport difference is how services are started.

## Docs

There is a real Material for MkDocs site (`mkdocs.yml` + `docs/`) and a
`CHANGELOG.md`. **Keep both current when behaviour changes** — `docs/transports.md`
covers the transport layer, `docs/cli.md` the commands, `docs/configuration.md`
the YAML schema. `docs/changelog.md` is a one-line snippet include of the root
`CHANGELOG.md`, so there is a single source of truth; edit the root file.
Verify with `uv run mkdocs build --strict`. (`docs/timing.md` is empty and not in
the nav.)

## Known rough edges

- The `apps/procurement/*.yml` partner blocks use `address:` but `PartnerModel` expects `host:` (`models.py`); the unknown key is ignored and `host` defaults to `127.0.0.1`. `builtin` now normalises this when it rewrites configs, and the parity tests generate correct `host:` configs — but the checked-in YAML is still wrong, so anything reading it directly inherits the bug.
- `negmasd builtin` treats its ports as *preferred*: a busy port is replaced with a free one and the app's configs are rewritten to match (`_materialise_configs` in `cli.py`), so launch ports and config contents cannot disagree. It prints one machine-readable `builtin ready: {...}` line (via `typer.echo`, **not** `rich.print`, which would wrap it) — that is how tests and scripts discover the ports.
