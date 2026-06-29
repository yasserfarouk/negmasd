# Quickstart

## Install

negmasd uses [uv](https://docs.astral.sh/uv/). After cloning:

```bash
uv sync
```

This creates a virtual environment and installs negmasd and its dependencies (including a local editable [negmas](https://github.com/yasserfarouk/negmas)).

## Run the built-in procurement app

```bash
negmasd builtin procurement
```

This starts three FastAPI processes and loads their configs:

| Role   | Port |
|--------|------|
| runner | 9010 |
| buyer  | 8001 |
| seller | 8002 |

Then open the buyer's interactive docs at <http://127.0.0.1:8001/docs> and call:

1. **`/start-negotiation`** with `neg = trade`.

The buyer asks the seller to accept, then asks the runner to run the negotiation. On completion the result is delivered to both parties and the runner.

Inspect the result with **`/peak`** on the buyer (port 8001) or the runner (<http://127.0.0.1:9010/docs>).

## Modes

negotiations run **`distributed`** by default: every per-round decision for the
buyer is computed inside the buyer's process and the seller's inside the
seller's — the runner never sees either ufun.

To run a negotiation in the runner's process instead (the runner pulls each
party's strategy + ufun), set `mode: centralized` under the negotiation in
`src/negmasd/apps/procurement/agents/*.yml`:

```yaml
negotiations:
  trade:
    mode: centralized   # default is: distributed
    seed: 42            # optional: reproducible runs
    # ...
```

The agreement is identical either way (see [Exact parity](parity.md)).

## Run a ufun in its own process (optional)

Start a ufun service and point a negotiator's ufun at it:

```bash
negmasd ufun 9100 --config path/to/ufuns.yml
```

In the agent config, set the negotiation's `ufun` to a remote one:

```yaml
ufun:
  type: negmasd.ufuns.RemoteUtilityFunction
  base_url: http://127.0.0.1:9100
  ufun_id: seller
```

See [Configuration](configuration.md) for the full schema.

## Run the tests

```bash
uv run pytest                    # everything (includes subprocess full-stack tests)
uv run pytest -m "not slow"      # skip the subprocess tests
```

The suite asserts negmasd reaches the **same** agreement, step and timeout flag as plain negmas across many scenarios and both modes. See [Exact parity](parity.md).
