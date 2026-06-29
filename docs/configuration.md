# Configuration

Configs are YAML. Every negmas object is declared by a `type:` (dotted import path) plus its constructor kwargs as sibling keys — `MakableModel.make_object` deserializes them into live objects.

## Agent config

One file per agent. Maps negotiation names to the agent's *private* view of each negotiation (`PersonalNegotiationModel`).

```yaml
name: seller
host: 127.0.0.1
port: 8002
runner_host: 127.0.0.1
runner_port: 9010
negotiations:
  trade:
    name: trade
    mode: distributed          # distributed (default) | centralized
    seed: 42                   # optional; reproducible runs
    partners:
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
    `PartnerModel` expects `host:`. An `address:` key is silently ignored and `host` defaults to `127.0.0.1`.

### Public vs. private

`negotiator` and `ufun` are private. Before contacting partners or the runner, the initiator calls `remove_personal_info()`, which sends only: `name`, `partners`, `outcome_space`, `mechanism`, `random_order`, `mode`, `seed`. In distributed mode each partner re-builds its own private negotiator+ufun from its own config.

## Runner config

```yaml
name: runner
port: 9010
keep_trace: true   # store the full negotiation trace in the runner's memory
```

## Remote ufun

To run a ufun in its own process, point the negotiation's `ufun` at a ufun service:

```yaml
ufun:
  type: negmasd.ufuns.RemoteUtilityFunction
  base_url: http://127.0.0.1:9100
  ufun_id: seller
```

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
| `partners` | — | list of `{name, host, port}` |
| `outcome_space` | `DiscreteCartesianOutcomeSpace` | shared outcome space |
| `mechanism` | `SAOMechanism` | mechanism type + kwargs (`n_steps`, `time_limit`, …) |
| `mode` | `distributed` | `distributed` or `centralized` |
| `seed` | `None` | seed applied right before `mechanism.run()` |
| `random_order` | `false` | shuffle partner order at start |
| `negotiator` *(private)* | `BoulwareNegotiator` | strategy type + kwargs |
| `ufun` *(private)* | — | utility function type + kwargs |
