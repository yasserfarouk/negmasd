# CLI

The `negmasd` command (a [Typer](https://typer.tiangolo.com/) app) starts the services.

Every start command takes `--protocol http|grpc|mqtt` (default `http`); `grpc` and
`mqtt` need their optional extras, and `mqtt` also needs `--broker` — see
[Transports](transports.md). `builtin` supports `http` and `grpc` only.

```bash
negmasd --help
```

## `negmasd builtin <app>`

Launch all processes for a built-in app and load their configs.

```bash
negmasd builtin procurement
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--base-port` | 8000 | agents *prefer* `base_port + 1, +2, …` |
| `--runner-port` | 9010 | preferred runner port; pass `0` to use an already-running runner |
| `--protocol` | `http` | serve every role over `http` or `grpc` (not `mqtt`, see below) |
| `--host` | `127.0.0.1` | address written into the generated configs |
| `--load-configs / --no-load-configs` | load | rewrite and load each config after startup |

Apps live in `negmasd/apps/<app>/` (`runner.yml` + `agents/*.yml`).

### Ports are preferred, not required

The bundled YAML hardcodes ports, which used to make `builtin` fail outright if
anything else held one: every server logged *address already in use* and the
config push then hit a runner that never listened.

Now each port is a preference. If it is taken, `builtin` picks a free one and
**rewrites copies of the app's configs** — each agent's own port, its runner, and
every partner entry (matched by name) — so the pieces still address each other.
It then waits for each service to answer before loading configs, and prints:

```console
builtin ready: {"protocol": "http", "host": "127.0.0.1", "runner": 9010, "agents": {"buyer": 8001, "seller": 8002}, "configs": "/tmp/negmasd-procurement-ab12cd"}
```

That line is emitted unwrapped on one line, so scripts can parse it. `configs`
is the temporary directory holding the rewritten YAML; it is removed when
`builtin` exits, and `builtin` shuts its child servers down cleanly on `SIGTERM`
or Ctrl-C rather than orphaning them.

`--no-load-configs` opts out of the rewriting — and therefore out of port
substitution, since a substituted port is only safe *because* the configs are
rewritten to match it. In that mode a busy preferred port is an error:

```console
$ negmasd builtin procurement --no-load-configs --runner-port 9010
Preferred ports already in use: runner (9010).
With --no-load-configs the configs cannot be rewritten, so substituting a free
port would leave the agents pointing at the wrong places. Free those ports, pick
others with --base-port / --runner-port, or drop --no-load-configs.
```

`--protocol mqtt` is rejected here too: `builtin` exists to allocate and substitute
real ports, and MQTT has none (`host:port` is a topic identifier there). It exits
non-zero, naming the single-role commands to run instead.

## `negmasd runner`

Start a single runner. You need exactly one running.

```bash
negmasd runner --port 9010
negmasd runner --port 9010 --protocol grpc
negmasd runner --port 9010 --protocol mqtt --broker broker.local:1883
```

Under MQTT, `--host` (default `127.0.0.1`) is this service's *identity* as its peers
have it configured — it forms the topic `negmasd/<host>_<port>/rpc` and is not a
bind address. It must match the partner entries that name this service.

## `negmasd agent`

Start a single agent. If `--config` is given it is loaded automatically once the server is ready (in a background thread).

```bash
negmasd agent 8001 --config path/to/buyer.yml
negmasd agent 8001 --config path/to/buyer.yml --protocol grpc
negmasd agent 8001 --config buyer.yml --protocol mqtt --broker broker.local:1883
```

## `negmasd ufun`

Start a utility-function service for `RemoteUtilityFunction` clients.

```bash
negmasd ufun 9100 --config path/to/ufuns.yml
```

If `--config` is omitted, load ufuns later with `GET /load-config?path=...`.

## Manual multi-machine launch

The CLI is convenience over HTTP; you can run each role anywhere and wire them by host/port in the configs:

```bash
# machine A
negmasd runner --port 9010
# machine B
negmasd agent 8001 --config buyer.yml
# machine C
negmasd agent 8002 --config seller.yml
```

Then `GET http://<machine-B>:8001/start-negotiation?neg=trade`. As long as the configs reference each other's reachable host/port — and `protocol`, if not HTTP — the negotiation runs across the three machines.

Because the protocol is per-address, machines need not agree on one: a role
served with `--protocol grpc` just has to be addressed with `protocol: grpc` in
the configs that name it.
