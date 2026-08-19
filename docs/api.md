# API reference

Each role declares a *method table*; what follows is its **HTTP projection**.
Served over HTTP, every role is a FastAPI app and exposes interactive docs at
`/docs`. The endpoints below are what the roles call on each other; you normally
only invoke `/load-config`, `/start-negotiation`, and `/peak` yourself.

Over [gRPC](transports.md) the same methods are reached through one generic
`Call` RPC whose `method` field is the path without its leading slash
(`respond`, `accept-negotiation`, …) and whose payload is the same JSON. There is
no GET/POST distinction there, so the `Method` column below is HTTP-only.

## Agent

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | liveness |
| `GET` | `/load-config?path=…` | load this agent's `AgentConfigModel` from a YAML file |
| `GET` | `/start-negotiation?neg=…` | **initiate** a negotiation by name (runs the accept → run handshake) |
| `POST` | `/accept-negotiation` | partner asked to accept a negotiation (public info) |
| `GET` | `/neg-strategy?negid=…` | **centralized**: return this party's strategy + ufun to the runner |
| `POST` | `/init-negotiation` | **distributed**: build & store this party's live negotiator |
| `POST` | `/respond` | **distributed**: run one round — wire `SAOState` in, wire `SAOResponse` out |
| `POST` | `/receive-results` | receive final agreement + state |
| `GET` | `/peak` | dump this agent's in-memory sessions |

!!! note "`/root` is an alias for `/`"
    gRPC dispatches by method *name*, and the liveness method is named `root`, so
    it is published at `/root` as well as `/` to keep the two transports
    interchangeable. The alias is hidden from `/docs`.

## Runner

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | liveness |
| `GET` | `/load-config?path=…` | load runner config (`keep_trace`, …) |
| `POST` | `/run-negotiation` | run a negotiation (dispatches on `mode`) |
| `GET` | `/peak` | dump stored sessions (final state, agreement, trace) |

## ufun service

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | liveness |
| `GET` | `/load-config?path=…` | load `ufun_id → ufun` mapping from YAML |
| `POST` | `/meta` | `{ufun_id}` → `{reserved_value}` |
| `POST` | `/eval` | `{ufun_id, outcome}` → `{value}` |

## Wire payloads (distributed mode)

Identical on both transports: gRPC carries this same JSON inside its envelope.

`/respond` request — a wire-encoded `SAOState`:

```json
{
  "negid": "trade-…",
  "state": {
    "step": 3,
    "relative_time": 0.16,
    "current_offer": ["200", 4],
    "current_proposer": "buyer",
    "n_acceptances": 0,
    "new_offers": [["buyer", ["200", 4]]]
  }
}
```

`/respond` response — a wire-encoded `SAOResponse` (`response` is the `ResponseType` integer; `0` = ACCEPT, `1` = REJECT, `2` = END, … and outcomes are arrays, re-tupled on decode):

```json
{ "response": 1, "outcome": ["150", 6], "data": null }
```
