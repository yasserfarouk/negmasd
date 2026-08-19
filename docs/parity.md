# Exact parity

negmasd's correctness bar is **exact parity**: the same scenario, run distributedly through negmasd, must reach the *same* agreement, step, and timeout flag as an ordinary in-process negmas run.

This is stronger than approximate behaviour-matching. It is possible because negmasd reuses negmas's own `SAOMechanism` and only relocates *where* each decision is computed — it never reimplements the protocol or the clock.

## Why it is exact

A time-based negotiator's response is a pure function of:

- the **state passed to it** that round (`relative_time`, `current_offer`, …), and
- its **own ufun**,

and **not** of the mechanism's internal clock. So if the runner's mechanism produces the exact per-round states it would in-process, and each is fed to the party's real negotiator, the sequence of decisions — and therefore the outcome — is identical.

This was established empirically by recording the state→response sequence of a negotiator inside a real `SAOMechanism` and replaying it into a standalone instance: identical responses, every time.

## The two wire invariants

The exactness depends on the JSON wire (`negmasd.wire`) being faithful. Two things bite:

1. **The `ResponseType` enum.** `negmas.serialization.serialize` drops it to `{}`, silently discarding every ACCEPT. The wire carries `int(response.value)` instead.
2. **Outcome tuples.** JSON has only arrays. An outcome decoded as a list makes `ufun(offer)` return `None`, so the negotiator rejects everything and the negotiation always times out. The wire **re-tuples** every outcome on decode.

Both are regression-tested.

## Determinism and the seed

The only source of randomness that affects the outcome lives in the **runner's** mechanism (e.g. random first-proposer selection). The agents' `stochastic=False` negotiators draw no RNG — parity holds even when the agent-side RNG is deliberately poisoned.

So reproducibility needs just one thing: the runner seeds `random` immediately before `mechanism.run()`. Set the negotiation's `seed` to do this. The parity tests rely on it to align negmasd with the seeded reference.

## Cross-version / cross-machine

Because the wire transmits **values** as JSON (never pickled objects, never indices), and because each agent builds its own negotiator + ufun from its own config in its own environment, the runner never deserializes an agent's objects. Agents can therefore run **different Python versions** or on **different machines**.

## The proven envelope

Exact parity is proven for:

- **`n_steps`-bounded** negotiations,
- **`stochastic=False`**, time-based negotiators (Boulware / Aspiration),
- negotiators that do not depend on a mechanism interface (`self.nmi`) or lifecycle callbacks.

Outside the envelope:

- **`time_limit`** is supported in distributed mode and behaves correctly, but is **not bit-exact** with an in-process run — per-round network latency shifts the wall-clock `relative_time`. This is expected and fine; exact parity is only claimed for `n_steps`. It also means a `time_limit` run may differ between transports, since their latencies differ — noticeably for MQTT, where every round is two broker hops.
- The agent's negotiator currently receives only `__call__(state)` — no `join`, no `on_negotiation_start` / `on_round_start` / `on_negotiation_end`, no `self.nmi`. Strategies that read those need the agent extended (e.g. host the negotiator in a local mirror mechanism).
- The wire carries the `SAOState` fields a time-based negotiator reads, not the full state (`current_data` / `new_data` / `threads` / `results` are dropped). Protocols using those need the codec extended.

## The test suite

`tests/test_negmas_parity.py` runs, for many seeds and both modes, the same scenario in negmas-proper and negmasd and asserts equality of `(agreement, step, timedout)`:

- bilateral and multilateral,
- varied outcome spaces,
- the remote-ufun path,
- the **timeout / no-agreement** branch, with the invariant `not (agreement and timedout)`.

`tests/test_subprocess_parity.py` proves the **cross-process wire** end-to-end: it launches the runner and agents as real `uvicorn` subprocesses and drives negotiations over HTTP — deal and timeout, in both modes.

`tests/test_grpc_parity.py` and `tests/test_mqtt_parity.py` do the same over
[gRPC and MQTT](transports.md) — in-process servers and real CLI subprocesses, both
modes, plus the remote-ufun path — so the choice of transport is proven not to
change the outcome. Scenario, configs and driving are shared between them
(`tests/transport_harness.py`), so the only difference is how services are started.
The gRPC suite also asserts that the HTTP and gRPC adapters expose exactly the same
method set, since both are projections of one table. These cases skip when the
relevant optional extra is absent.

`tests/test_transport_optional.py` checks the guarantees that keep `grpcio`
optional (nothing on the HTTP path imports it; a missing extra yields one
actionable error) by blocking the import in a subprocess, so it runs either way.

```bash
uv run pytest                 # all of it
uv run pytest -m "not slow"   # skip the subprocess tests
```
