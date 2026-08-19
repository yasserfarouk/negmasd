# Configuration

Configs are YAML. Every negmas object is declared by a `type:` (dotted import path) plus its constructor kwargs as sibling keys — `MakableModel.make_object` deserializes them into live objects.

## Agent config

One file per agent. Maps negotiation names to the agent's *private* view of each negotiation (`PersonalNegotiationModel`).

```yaml
name: seller
host: 127.0.0.1
port: 8002
protocol: http               # how THIS agent serves: http | grpc | mqtt
runner_host: 127.0.0.1
runner_port: 9010
runner_protocol: http        # how to REACH the runner
negotiations:
  trade:
    name: trade
    mode: distributed          # distributed (default) | centralized
    seed: 42                   # optional; reproducible runs
    partners:
      # `protocol` is how to reach that partner; omit for http
      - { name: buyer,  host: 127.0.0.1, port: 8001 }
      - { name: seller, host: 127.0.0.1, port: 8002 }
    outcome_space:
      type: negmas.outcomes.DiscreteCartesianOutcomeSpace
      issues:
        - { name: price,    type: negmas.outcomes.DiscreteCardinalIssue, values: [100, 150, 200, 250, 300] }
        - { name: quantity, type: negmas.outcomes.ContiguousIssue,       values: [1, 10] }
    mechanism:
      type: negmas.sao.SAOMechanism
      n_steps: 100             # use n_steps for exact parity
      # time_limit: 60         # allowed in distributed mode, but not bit-exact
    negotiator:                # PRIVATE — never sent to other parties
      type: negmas.sao.negotiators.BoulwareTBNegotiator
      name: seller
    ufun:                      # PRIVATE
      type: negmas.preferences.crisp.linear.LinearAdditiveUtilityFunction
      weights: [0.00166667, 0.05]
      values:
        - { type: negmas.preferences.value_fun.LinearFun, slope: 1 }
        - { type: negmas.preferences.value_fun.LinearFun, slope: 1 }
      reserved_value: 0.1
```

!!! warning "Use `host:` not `address:`"
    `PartnerModel` expects `host:`. An `address:` key is silently ignored and `host`
    then quietly defaults to `127.0.0.1` — which looks fine locally and breaks the
    moment anything is remote. (`negmasd builtin` normalises `address:` to `host:`
    in the configs it generates, but hand-written configs are on their own.)

!!! note "Protocols must agree"
    A role served with `--protocol grpc` must be addressed with `protocol: grpc`
    wherever it is named, and vice versa. All default to `http`, and the protocol is
    per-address, so a deployment may mix them. See [Transports](transports.md).

!!! note "MQTT needs nothing else in the config"
    Setting `protocol: mqtt` is the *only* config change: the topic is derived from
    each peer's `host`/`port`, and the broker is given on the command line
    (`--broker`) rather than per peer. Note that under MQTT `host`/`port` are an
    identifier, not a TCP endpoint — see [Transports](transports.md).

### Public vs. private

`negotiator` and `ufun` are private. Before contacting partners or the runner, the initiator calls `remove_personal_info()`, which sends only: `name`, `partners`, `outcome_space`, `mechanism`, `random_order`, `mode`, `seed`. In distributed mode each partner re-builds its own private negotiator+ufun from its own config.

## Runner config

```yaml
name: runner
port: 9010
keep_trace: true   # store the full negotiation trace in the runner's memory
```

The runner is told which protocol to serve on the command line
(`negmasd runner --protocol …`), not in this file; agents name it via their own
`runner_protocol`.

## Remote ufun

To run a ufun in its own process, point the negotiation's `ufun` at a ufun service:

```yaml
ufun:
  type: negmasd.ufuns.RemoteUtilityFunction
  base_url: http://127.0.0.1:9100
  ufun_id: seller
```

The scheme in `base_url` selects the protocol, so `grpc://127.0.0.1:9100` (or
`mqtt://127.0.0.1:9100`) reaches a ufun service started with that `--protocol`. You
may give `host`, `port` and `protocol` separately instead of `base_url`.

The ufun service is loaded from a YAML file mapping `ufun_id` → a ufun model:

```yaml
# ufuns.yml — loaded by:  negmasd ufun 9100 --config ufuns.yml
seller:
  type: negmas.preferences.crisp.linear.LinearAdditiveUtilityFunction
  outcome_space:                 # include so the service can build it
    type: negmas.outcomes.DiscreteCartesianOutcomeSpace
    issues: [ ... ]
  weights: [0.00166667, 0.05]
  values: [ ... ]
  reserved_value: 0.1
```

## Negotiation fields reference

| Field | Default | Meaning |
|-------|---------|---------|
| `name` | — | negotiation name (key used by `/start-negotiation?neg=`) |
| `partners` | — | list of `{name, host, port, protocol}` (`protocol` defaults to `http`) |
| `outcome_space` | `DiscreteCartesianOutcomeSpace` | shared outcome space |
| `mechanism` | `SAOMechanism` | mechanism type + kwargs (`n_steps`, `time_limit`, …) |
| `mode` | `distributed` | `distributed` or `centralized` |
| `seed` | `None` | seed applied right before `mechanism.run()` |
| `random_order` | `false` | shuffle partner order at start |
| `negotiator` *(private)* | `BoulwareNegotiator` | strategy type + kwargs |
| `ufun` *(private)* | — | utility function type + kwargs |

Agent-level fields (outside `negotiations`):

| Field | Default | Meaning |
|-------|---------|---------|
| `name` | — | this agent's name; partner entries are matched to it by name |
| `host` / `port` | `127.0.0.1` / — | where this agent serves |
| `protocol` | `http` | protocol this agent serves on |
| `runner_host` / `runner_port` | `127.0.0.1` / `9010` | where the runner is |
| `runner_protocol` | `http` | protocol to use when calling the runner |
