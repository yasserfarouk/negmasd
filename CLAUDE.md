# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`negmasd` distributes a [negmas](https://github.com/yasserfarouk/negmas) negotiation across processes/machines. Each role is a FastAPI app driven by HTTP. The goal is to run any negmas mechanism (primarily bilateral `SAOMechanism`) with each negotiator — and optionally each ufun — in its own process.

`negmas` is consumed as an **editable local dependency** at `../negmas` (see `[tool.uv.sources]` in `pyproject.toml`). Changes there affect this project directly.

## Commands

```bash
uv sync                              # install (creates .venv)
negmasd --help                       # CLI entry point (typer app)
negmasd builtin procurement          # start runner(9010) + buyer(8001) + seller(8002), auto-load configs
negmasd runner --port 9010           # start a single runner service
negmasd agent <port> --config <f>    # start a single agent service (auto-loads config when ready)
negmasd ufun <port> --config <f>     # start a utility-function service (for remote ufuns)
.venv/bin/python -m pytest tests/    # run the test suite (pytest 9.x in the venv)
.venv/bin/python -m pytest tests/ -m "not slow"   # skip the subprocess full-stack tests
.venv/bin/python ...                 # invoke python directly; `python` is not on PATH
```

No linter or build step is configured. `docs/timing.md` is empty.

To exercise a negotiation: run `negmasd builtin procurement`, open `http://127.0.0.1:8001/docs`, call `/start-negotiation?neg=trade`, then `/peak` on any process to inspect results.

## Architecture

Three process roles, each a FastAPI app:

- **Agent** (`agent.py`) — one per negotiator. Holds that party's **private** info (its `negotiator` strategy + `ufun`) in an `AgentConfigModel`, keyed by negotiation name.
- **Runner** (`runner.py`) — single shared service that builds and runs the negmas `Mechanism`.
- **CLI** (`cli.py`) — spawns processes and pushes configs via `/load-config`.

**Handshake for one negotiation** (see `agent.start_negotiation` → `runner.run_negotiation` → `runner.async_run`):
1. Initiator agent mints a `negid`, strips private fields via `PersonalNegotiationModel.remove_personal_info()` → public `NegotiationModel`, and POSTs `/accept-negotiation` to each partner.
2. On all-accept, it POSTs `/run-negotiation` to the runner.
3. Runner GETs `/neg-strategy` from each partner to pull their private strategy+ufun, builds the mechanism, runs it, and POSTs `NegResultsModel` to each partner's `/receive-results`.

**Object construction from YAML** is the central pattern. `MakableModel.make_object` (`models.py`) feeds a model's dict — a `type:` field plus arbitrary extra fields (Config `extra="allow"`) — into negmas's `deserialize(..., python_class_identifier="type", remove_type_field=True)`. So any negmas class (mechanism, outcome space, negotiator, ufun) is specified declaratively in YAML by its dotted path under `type:` with constructor kwargs as siblings. Apps live in `apps/<name>/` as `runner.yml` + `agents/*.yml`.

All HTTP calls go through `common.get_response` (async httpx). State is in-memory only (module-level dicts: agent `sessions_*`, runner `SESSIONS`); no persistence.

## Execution modes (`NegotiationModel.mode`)

A negotiation runs in one of two modes; both reach **bit-exact parity** with negmas-proper (proven in `tests/`):

- **`distributed`** (default) — runner runs the real `SAOMechanism` but adds one `SAORemoteNegotiator` per partner (`negotiator.py`). The proxy holds **no ufun**; each round it POSTs the state to the partner's `/respond`, where the agent runs its real, persistent negotiator (built in `/init-negotiation`, stored in `agent.live_negotiators`). This keeps strategy + ufun in the partner's own process.
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

## Known rough edges

- The `apps/procurement/*.yml` partner blocks use `address:` but `PartnerModel` expects `host:` (`models.py`); the unknown key is ignored and `host` defaults to `127.0.0.1` — works only because everything is local. (The parity tests generate correct `host:` configs.)
- `common.get_response` catches `requests.exceptions.*` but the calls use `httpx`; only the final generic `except Exception` actually fires.
