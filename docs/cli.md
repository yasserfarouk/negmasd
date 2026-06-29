# CLI

The `negmasd` command (a [Typer](https://typer.tiangolo.com/) app) starts the services.

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
| `--base-port` | 8000 | agents listen on `base_port + 1, +2, …` |
| `--runner-port` | 9010 | runner port; pass `0` to skip starting a runner |
| `--load-configs / --no-load-configs` | load | load each config after startup |

Apps live in `negmasd/apps/<app>/` (`runner.yml` + `agents/*.yml`).

## `negmasd runner`

Start a single runner. You need exactly one running.

```bash
negmasd runner --port 9010
```

## `negmasd agent`

Start a single agent. If `--config` is given it is loaded automatically once the server is ready (in a background thread).

```bash
negmasd agent 8001 --config path/to/buyer.yml
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

Then `GET http://<machine-B>:8001/start-negotiation?neg=trade`. As long as the configs reference each other's reachable host/port, the negotiation runs across the three machines.
