# negmasd

Run any [negmas](https://github.com/yasserfarouk/negmas) negotiation **distributedly** — each negotiator (and, optionally, each utility function) in its own process or machine — while reaching **bit-exact parity** with an ordinary in-process negmas run.

negmasd reuses negmas's own `SAOMechanism`; it does not reimplement the protocol. The runner runs the real mechanism and forwards only the per-round decisions to the processes that own them. Only **values** cross the wire (plain JSON, never pickled objects), so the pieces can run under different Python versions or on different machines.

The wire is pluggable: **HTTP** (FastAPI) always, and **gRPC** as an optional extra.

## Installation

After installing [uv](https://docs.astral.sh/uv/), clone this repo then:

```bash
uv sync                 # HTTP only
uv sync --extra grpc    # ...plus the optional gRPC transport
```

`grpcio` is never required — everything works without it.

## The three roles

- **Agent** (`negmasd.agent`) — one per negotiator; holds that party's private strategy + ufun.
- **Runner** (`negmasd.runner`) — runs the real `SAOMechanism`; one shared service.
- **ufun service** (`negmasd.ufun_service`) — *(optional)* holds a ufun and evaluates outcomes for a `RemoteUtilityFunction`.

Each role is a plain Python service that declares a *method table*; a transport adapter projects it onto HTTP or gRPC, so negotiation logic never names a protocol.

An *app* is a folder of configs (`runner.yml` + `agents/*.yml`). See `src/negmasd/apps/procurement` for an example.

## Two execution modes

Each negotiation declares a `mode` (default `distributed`):

| Mode | What runs where |
|------|-----------------|
| **`centralized`** | the runner pulls each party's strategy + ufun and runs the whole negotiation locally |
| **`distributed`** | the runner runs the mechanism but forwards every per-round `negotiator(state)` to the party's own process — strategy + ufun never leave it |

Both reach exact parity with negmas-proper for `n_steps`-bounded negotiations.

## Quick try

```bash
negmasd builtin procurement
```

This starts a runner (9010), a buyer (8001) and a seller (8002) and loads their configs. Those ports are *preferred*: if one is taken, `builtin` picks a free port and rewrites the app's configs to match. It prints what it chose:

```console
builtin ready: {"protocol": "http", "host": "127.0.0.1", "runner": 9010, "agents": {"buyer": 8001, "seller": 8002}, "configs": "/tmp/negmasd-procurement-ab12cd"}
```

Open <http://127.0.0.1:8001/docs>, call `/start-negotiation` with `neg = trade`, then `/peak` (on the buyer or the runner at <http://127.0.0.1:9010/docs>) to see the result.

To run the whole thing over gRPC instead:

```bash
negmasd builtin procurement --protocol grpc
```

`--protocol http|grpc` works on `runner`, `agent` and `ufun` too. See the full CLI with:

```bash
negmasd --help
```

## Tests

negmasd is validated by **exact parity** against plain negmas: the same scenario, run distributedly, reaches the same agreement, step and timeout flag.

```bash
uv run pytest                 # everything (includes real-subprocess HTTP and gRPC tests)
uv run pytest -m "not slow"   # skip the subprocess tests
```

The gRPC cases skip automatically when the optional extra is not installed, and
`tests/test_transport_optional.py` asserts that nothing on the HTTP path imports
`grpc` at all.

## Documentation

Full docs (Material for MkDocs):

```bash
uv run mkdocs serve           # live preview at http://127.0.0.1:8000
uv run mkdocs build           # static site in ./site
```

Start with `docs/index.md`, then **Architecture**, **Execution modes**, **Exact parity** and **Transports**.

Release notes live in [CHANGELOG.md](CHANGELOG.md).
