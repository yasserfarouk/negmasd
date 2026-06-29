# Execution modes

Each negotiation declares a `mode` (default `distributed`). Both modes reach **bit-exact parity** with negmas-proper for step-bounded negotiations.

## `centralized`

The runner pulls each partner's strategy + ufun (via `GET /neg-strategy`), builds the negotiators locally, and runs the whole `SAOMechanism` in its own process.

- This **is** negmas running in one process — parity is trivially exact and validates config serialization fidelity.
- Strategy and ufun leave the partner's process (the runner sees them).
- Lowest latency; no per-round network traffic.

Use it when the parties trust the runner with their strategy/ufun, or for fast local simulation.

## `distributed`

The runner runs the real `SAOMechanism` but adds a `SAORemoteNegotiator` proxy per partner. Every per-round `negotiator(state)` is forwarded over JSON to the partner's `/respond`, where the partner's real negotiator runs.

- Strategy + ufun **stay in the partner's process** — the runner only ever sees a no-ufun proxy and the per-round response.
- Parties can run different Python versions / machines.
- Each round costs an HTTP round-trip.

Use it when negotiators are owned by different parties or must run elsewhere.

## Remote ufun (a third level)

Independently of the mode, a ufun can run in **yet another** process. `RemoteUtilityFunction` is a real `BaseUtilityFunction` whose `eval` is forwarded to a `negmasd.ufun_service` process; `reserved_value` (which negotiators read directly) is fetched once at construction.

```mermaid
flowchart LR
    N[negotiator] -- "preferences" --> RU[RemoteUtilityFunction]
    RU -- "/eval (outcome)" --> S[ufun service<br/>real ufun]
    S -- "value" --> RU
```

Because it is just another ufun `type` in config, an agent (or the centralized runner) builds it with no special-casing. It is exact: the same ufun object, evaluated in another process, returns the same values.

!!! note "Cost"
    A presorting negotiator enumerates the whole outcome space once to build its inverter, which becomes one `/eval` call per outcome. Fine for modest spaces; batch evaluation is a possible future optimization.

## Choosing a mode

| You want… | Mode |
|-----------|------|
| Fast local simulation, full trust | `centralized` |
| Keep each strategy/ufun in its owner's process | `distributed` |
| Keep the ufun itself in a separate (e.g. proprietary) process | any mode + `RemoteUtilityFunction` |

## Determinism

Only the **runner's** mechanism draws from the global RNG (e.g. a random first-proposer). Set the negotiation's `seed` and the runner seeds `random` immediately before running, for reproducible (and, against a seeded reference, bit-exact) runs. Agents running `stochastic=False` negotiators need no RNG coordination at all.
