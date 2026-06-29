# HTTP API

Every role is a FastAPI app, so each exposes interactive docs at `/docs`. The endpoints below are what the roles call on each other; you normally only invoke `/load-config`, `/start-negotiation`, and `/peak` yourself.

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
