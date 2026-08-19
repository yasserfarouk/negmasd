# Architecture

negmasd has three process roles. Each is a plain Python **service** holding no
transport code: it declares a *method table*, and a transport adapter projects
that table onto a wire protocol — [FastAPI](https://fastapi.tiangolo.com/) over
HTTP by default, or gRPC, or MQTT. See [Transports](transports.md).

This page is the mechanics. For the distinction that shapes everything below —
`centralized` vs. `distributed`, i.e. where each party's strategy and ufun live —
start at [Execution modes](modes.md).

| Role | Module | Holds | Responsibility |
|------|--------|-------|----------------|
| **Agent** | `negmasd.agent` | one party's *private* strategy + ufun | accept/run handshake; compute that party's per-round responses (distributed mode) |
| **Runner** | `negmasd.runner` | nothing private | build and run the real `SAOMechanism`; report results |
| **ufun service** | `negmasd.ufun_service` | a ufun (+ its outcome space) | evaluate outcomes for a `RemoteUtilityFunction` |

Each role module also exposes a process-wide `service` instance and an
`app = build_app(service)`, so `uvicorn negmasd.agent:app` works as before. State
lives on the instance rather than in module globals, which is what allows several
services in one process.

A negotiation **app** is a folder under `negmasd/apps/<name>/` containing `runner.yml` and `agents/*.yml`. The CLI's `builtin` command launches an app's processes and loads their configs.

## The negotiation handshake

The labels below are method names. Over HTTP they are the paths and verbs shown
in the [API reference](api.md); over gRPC they are the `method` field of the
generic `Call` RPC.

```mermaid
sequenceDiagram
    participant I as Initiator agent
    participant P as Partner agents
    participant R as Runner
    I->>P: POST /accept-negotiation (public info)
    P-->>I: accepted
    I->>R: POST /run-negotiation (public info)
    Note over R: build_mechanism()
    alt distributed
        R->>P: POST /init-negotiation (build live negotiator)
        loop each round
            R->>P: POST /respond (wire SAOState)
            P-->>R: wire SAOResponse
        end
    else centralized
        R->>P: GET /neg-strategy (pull strategy + ufun)
        Note over R: build negotiators locally, run
    end
    R->>I: POST /receive-results
    R->>P: POST /receive-results
```

The **initiator** strips its private fields (`PersonalNegotiationModel.remove_personal_info()`) before sending the public `NegotiationModel` to partners and the runner. Private strategy + ufun never appear in the public payload.

## Inside distributed execution

[Execution modes](modes.md) covers *what* the two modes are; this section is *how*
the distributed one is built. The runner builds the real `SAOMechanism` but adds one
**`SAORemoteNegotiator`** per partner instead of the partner's real strategy. The
proxy:

- holds **no ufun and no strategy**,
- on each `__call__(state)`, encodes the state, sends it to the partner's `respond` over that partner's protocol, and decodes the returned `SAOResponse`.

The partner's process builds its **real** negotiator once (in `/init-negotiation`, stored in `agent.live_negotiators`) and runs it on each `/respond` call. Because a time-based negotiator's decision is a pure function of the *passed* state plus its own ufun, feeding it the exact per-round states the mechanism produces reproduces an in-process run exactly.

```mermaid
flowchart LR
    subgraph Runner process
      M[SAOMechanism] --> X0[SAORemoteNegotiator P0]
      M --> X1[SAORemoteNegotiator P1]
    end
    subgraph Agent P0 process
      N0[real BoulwareTBNegotiator + ufun]
    end
    subgraph Agent P1 process
      N1[real BoulwareTBNegotiator + ufun]
    end
    X0 -- "/respond (JSON state)" --> N0
    N0 -- "JSON response" --> X0
    X1 -- "/respond (JSON state)" --> N1
    N1 -- "JSON response" --> X1
```

The synchronous mechanism loop runs in a worker thread (`asyncio.to_thread`) so the per-round blocking calls don't stall the runner's event loop. This is why a transport must offer a **blocking** call form as well as an awaitable one.

The connection to each peer is cached per address, so the per-round call reuses one already-open connection rather than dialling every round.

## The wire (`negmasd.wire`)

The runner and agents may run different Python versions or live on different machines, so the wire format is **plain JSON with an explicit schema** — never pickle, never `negmas.serialization.serialize` for runtime objects. Two invariants make it exact:

1. `ResponseType` is an `IntEnum`; it is carried as its integer `.value`. (negmas's own `serialize` drops it to `{}`, which would lose every ACCEPT.)
2. negmas outcomes are **tuples**; JSON has only arrays, so every outcome is **re-tupled on decode**. (A list left un-tupled makes `ufun(offer)` return `None`, so the negotiator silently rejects everything.)

See [Exact parity](parity.md) for the determinism and cross-machine story.

## Object construction from config

Every negmas object (mechanism, outcome space, negotiator, ufun) is declared in YAML by its dotted path under a `type:` field plus constructor kwargs. `MakableModel.make_object` (in `negmasd.models`) feeds that into negmas's `deserialize`. Because `RemoteUtilityFunction` is just another `type`, an agent builds a remote ufun with no special-casing — see [Configuration](configuration.md).
