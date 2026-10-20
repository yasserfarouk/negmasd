# Quickstart

## Install

negmasd uses [uv](https://docs.astral.sh/uv/). After cloning:

```bash
uv sync
```

This creates a virtual environment and installs negmasd and its dependencies (including [negmas](https://github.com/yasserfarouk/negmas) from PyPI).

To also install the optional [gRPC transport](transports.md):

```bash
uv sync --extra grpc
```

Everything works without it; `grpcio` is never required.

## Run the built-in procurement app

```bash
negmasd builtin procurement
```

This starts three processes and loads their configs:

| Role   | Preferred port |
|--------|----------------|
| runner | 9010 |
| buyer  | 8001 |
| seller | 8002 |

Those are *preferred* ports. If one is already taken, `builtin` picks a free port
instead and rewrites the app's configs to match, so the parties still find each
other. Either way it prints one machine-readable line telling you what it used:

```console
builtin ready: {"protocol": "http", "host": "127.0.0.1", "runner": 9010, "agents": {"buyer": 8001, "seller": 8002}, "configs": "/tmp/negmasd-procurement-ab12cd"}
```

Then open the buyer's interactive docs at <http://127.0.0.1:8001/docs> (use the
port from that line) and call:

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

The agreement is identical either way (see [Exact parity](parity.md)). The two
modes are laid out side by side, with diagrams, under
[Execution modes](modes.md).

## Run it over gRPC

With the extra installed, serve every role over gRPC instead:

```bash
negmasd builtin procurement --protocol grpc
```

`builtin` also writes the matching `protocol:` fields into the configs it hands
the agents. For hand-rolled deployments you set them yourself — see
[Transports](transports.md). The outcome is the same over either protocol.

## Run a ufun in its own process (optional)

Start a ufun service and point a negotiator's ufun at it:

```bash
negmasd ufun 9100 --config path/to/ufuns.yml
```

In the agent config, set the negotiation's `ufun` to a remote one:

```yaml
ufun:
  type: negmasd.ufuns.RemoteUtilityFunction
  base_url: http://127.0.0.1:9100   # or host/port/protocol
  ufun_id: seller
```

See [Configuration](configuration.md) for the full schema.

## Run the tests

```bash
uv run pytest                    # everything (includes subprocess full-stack tests)
uv run pytest -m "not slow"      # skip the subprocess tests
```

The suite asserts negmasd reaches the **same** agreement, step and timeout flag as plain negmas across many scenarios and both modes. See [Exact parity](parity.md).
