from pathlib import Path
import json
import multiprocessing
import shutil
import signal
import socket
import tempfile
from time import sleep, time
import typer
from rich import print
from yaml import safe_dump, safe_load

from negmasd.common import (
    SUCCESS,
    RUNNER_DEFAULT_PORT,
    AGENT_BASE_PORT,
)
from negmasd.constants import DEFAULT_PROTOCOL
from negmasd.transport import Address, TransportError, channel_for, serve_service

app = typer.Typer()

PROTOCOL_HELP = (
    "Transport protocol to serve on: 'http' (FastAPI/uvicorn) or 'grpc'"
    " (requires the optional grpcio extra: uv sync --extra grpc)."
)


def _load_config_after_start(
    address: Address, config: Path, delay: float = 2.0
) -> None:
    """Poll-load a config once the server is up (runs in a background thread).

    Goes through a channel rather than raw HTTP, so it works for every protocol.
    """
    sleep(delay)
    channel = channel_for(address)
    for _ in range(30):
        try:
            print(channel.call("load-config", {"path": str(config)}, http_method="get"))
            return
        except Exception:
            sleep(0.5)
    print(f"Could not load config {config} into {address}")


def _serve(
    module: str, port: int, protocol: str, host: str = "0.0.0.0", **kwargs
) -> None:
    """Serve one role over ``protocol``.

    For HTTP the uvicorn import string is passed through unchanged, preserving
    the previous startup behaviour exactly.
    """
    from importlib import import_module

    service = import_module(f"negmasd.{module}").service
    if protocol == DEFAULT_PROTOCOL:
        kwargs["app_path"] = f"negmasd.{module}:app"
    try:
        serve_service(service, host=host, port=port, protocol=protocol, **kwargs)
    except TransportError as e:
        # An unavailable optional transport is a configuration problem, not a
        # crash: report it as one line and exit non-zero.
        print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


def _autoload(module: str, port: int, host: str, protocol: str, config: Path) -> None:
    """Serving blocks, so load the config from a background thread."""
    import threading

    threading.Thread(
        target=_load_config_after_start,
        args=(Address(host=host, port=port, protocol=protocol), config),
        daemon=True,
    ).start()


@app.command()
def agent(
    port: int,
    config: Path | None = None,
    host: str = "127.0.0.1",
    protocol: str = typer.Option(DEFAULT_PROTOCOL, help=PROTOCOL_HELP),
):
    """
    Start the agent server. If a config is given it is loaded automatically once
    the server is ready; otherwise load it later via GET /load-config?path=...
    """
    print(f"running {protocol} agent server on port {port} with config from {config}")
    if config is not None:
        _autoload("agent", port, host, protocol, config)
    _serve("agent", port, protocol)


@app.command()
def runner(
    port: int = RUNNER_DEFAULT_PORT,
    protocol: str = typer.Option(DEFAULT_PROTOCOL, help=PROTOCOL_HELP),
):
    """
    Start the session runner server. You must have one of these running.
    """
    print(f"running {protocol} runner server on port {port}")
    _serve("runner", port, protocol)


@app.command()
def ufun(
    port: int,
    config: Path | None = None,
    host: str = "127.0.0.1",
    protocol: str = typer.Option(DEFAULT_PROTOCOL, help=PROTOCOL_HELP),
):
    """
    Start a utility-function service. Negotiators can point a
    `negmasd.ufuns.RemoteUtilityFunction` at it so the ufun runs in its own
    process/machine. Optionally pass a config (YAML mapping ufun_id -> ufun
    model) to preload ufuns.
    """
    print(f"running {protocol} ufun service on port {port}")
    if config is not None:
        _autoload("ufun_service", port, host, protocol, config)
    _serve("ufun_service", port, protocol)


@app.command()
def builtin(
    app: str,
    base_port: int = AGENT_BASE_PORT,
    runner_port: int = RUNNER_DEFAULT_PORT,
    load_configs: bool = True,
    protocol: str = typer.Option(DEFAULT_PROTOCOL, help=PROTOCOL_HELP),
    host: str = "127.0.0.1",
):
    """
    Run a bundled app: one runner plus one agent per `agents/*.yml`.

    Ports are the *preferred* ports; any that is already taken is replaced with a
    free one and the configs are rewritten to match (see `_materialise_configs`),
    so a busy 9010 no longer sinks the whole app. The chosen ports are printed as
    a single `builtin ready: {...}` JSON line.

    Pass --runner-port=0 to use an already-running runner instead of starting one.
    """
    print(f"running builtin app {app} over {protocol}")
    # Default SIGTERM handling exits without unwinding, which would orphan the
    # child servers and leave the generated config dir behind. Turn it into a
    # normal exit so the `finally` below actually runs.
    signal.signal(signal.SIGTERM, _raise_system_exit)
    app_dir = Path(__file__).parent / "apps" / app
    runner_files = list(app_dir.glob("runner.yml")) + list(app_dir.glob("runner.yaml"))
    agents_dir = app_dir / "agents"
    agent_files = sorted(agents_dir.glob("*.yml")) + sorted(agents_dir.glob("*.yaml"))
    if not agent_files:
        print(f"No agent files found in {agents_dir}")
        return
    print(f"Will use config files:\n\t{chr(10).join(str(_) for _ in agent_files)}")

    # ------------------------------------------------------------------ ports
    taken: set[int] = set()
    start_runner = bool(runner_port)
    if not load_configs:
        # Substituting a port is only safe because the configs get rewritten to
        # match. --no-load-configs opts out of that, so a busy port here would
        # silently leave the parties addressing the wrong places: refuse instead.
        wanted = ([("runner", runner_port)] if start_runner else []) + [
            (f.stem, base_port + i + 1) for i, f in enumerate(agent_files)
        ]
        busy = [f"{what} ({port})" for what, port in wanted if not _port_free(port)]
        if busy:
            print(
                f"[red]Preferred ports already in use: {', '.join(busy)}.\n"
                "With --no-load-configs the configs cannot be rewritten, so"
                " substituting a free port would leave the agents pointing at the"
                " wrong places. Free those ports, pick others with --base-port /"
                " --runner-port, or drop --no-load-configs.[/red]"
            )
            raise typer.Exit(code=1)
    if start_runner:
        runner_actual = _pick_port(runner_port, taken, "runner")
    else:
        # Using someone else's runner: take its port from the app's own configs
        # rather than inventing one.
        runner_actual = _configured_runner_port(agent_files, runner_files)
        print(f"not starting a runner; expecting one on port {runner_actual}")
    agent_ports = [
        _pick_port(base_port + i + 1, taken, f"agent {f.stem}")
        for i, f in enumerate(agent_files)
    ]

    # ----------------------------------------------------------------- configs
    if load_configs:
        cfg_dir = Path(tempfile.mkdtemp(prefix=f"negmasd-{app}-"))
        runner_cfg, agent_cfgs = _materialise_configs(
            runner_files, agent_files, runner_actual, agent_ports, host, protocol, cfg_dir
        )
        print(f"rewrote configs for the chosen ports in {cfg_dir}")
        # (that path is also in the `builtin ready` line below, unwrapped)
    else:
        cfg_dir = None
        runner_cfg, agent_cfgs = None, []
        print("Skipping config rewriting/loading; using the YAML as written.")

    # ---------------------------------------------------------------- processes
    processes: list[multiprocessing.Process] = []
    if start_runner:
        processes.append(_spawn("runner", runner_actual, protocol))
    for port in agent_ports:
        processes.append(_spawn("agent", port, protocol))

    try:
        if load_configs:
            for port, cfg in [(runner_actual, runner_cfg)] * start_runner + list(
                zip(agent_ports, agent_cfgs)
            ):
                address = Address(host=host, port=port, protocol=protocol)
                _wait_ready(address)
                resp = channel_for(address).call(
                    "load-config", {"path": str(cfg)}, http_method="get"
                )
                if not isinstance(resp, dict) or resp.get("status") != SUCCESS:
                    print(f"[red]Failed to load {cfg} into {address}: {resp}[/red]")
                else:
                    print(f"loaded {cfg.name} into {address}")
        else:
            for port in [runner_actual] * start_runner + agent_ports:
                _wait_ready(Address(host=host, port=port, protocol=protocol))

        summary = {
            "protocol": protocol,
            "host": host,
            "runner": runner_actual,
            "agents": {f.stem: port for f, port in zip(agent_files, agent_ports)},
            "configs": None if cfg_dir is None else str(cfg_dir),
        }
        # One machine-readable line: with ports now negotiable, callers (and
        # tests) need to be told which ones were actually used. Emitted with
        # typer.echo, not rich.print, which would wrap it at the console width
        # and break anyone parsing it.
        typer.echo(f"builtin ready: {json.dumps(summary)}")
        for pr in processes:
            pr.join()
    finally:
        for pr in processes:
            if pr.is_alive():
                pr.terminate()
        for pr in processes:
            pr.join(timeout=10)
        if cfg_dir is not None:
            # Safe: each service reads its config once at load time.
            shutil.rmtree(cfg_dir, ignore_errors=True)


def _raise_system_exit(signum, frame) -> None:  # noqa: ARG001 - signal handler
    raise SystemExit(0)


def _spawn(module: str, port: int, protocol: str) -> multiprocessing.Process:
    """Start one role in its own process, over ``protocol``."""
    print(f"running {protocol} {module} server on port {port}")
    process = multiprocessing.Process(
        target=_serve, args=(module, port, protocol), daemon=False
    )
    process.start()
    return process


def _port_free(port: int, host: str = "0.0.0.0") -> bool:
    """Can a server actually bind this port on the interface it will use?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int, taken: set[int], what: str) -> int:
    """Return ``preferred`` if it is usable, else a free ephemeral port.

    `builtin` used to hard-fail when anything else held 9010/8001/8002 -- the
    servers reported "address already in use" and the config push then failed
    against a runner that was never listening.
    """
    if preferred and preferred not in taken and _port_free(preferred):
        taken.add(preferred)
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]
    taken.add(port)
    if preferred:
        print(f"[yellow]port {preferred} for {what} is busy; using {port}[/yellow]")
    return port


def _configured_runner_port(agent_files: list[Path], runner_files: list[Path]) -> int:
    """The runner port the app's own YAML expects (for --runner-port=0)."""
    for path in agent_files:
        cfg = safe_load(path.read_text()) or {}
        if cfg.get("runner_port"):
            return int(cfg["runner_port"])
    for path in runner_files:
        cfg = safe_load(path.read_text()) or {}
        if cfg.get("port"):
            return int(cfg["port"])
    return RUNNER_DEFAULT_PORT


def _materialise_configs(
    runner_files: list[Path],
    agent_files: list[Path],
    runner_port: int,
    agent_ports: list[int],
    host: str,
    protocol: str,
    out_dir: Path,
) -> tuple[Path, list[Path]]:
    """Write copies of the app's configs that agree with the ports we chose.

    The bundled YAML hardcodes ports and partner addresses, so anything other
    than the historical 9010/8001/8002 used to leave the agents pointing at the
    wrong places. Every agent's own port, its runner, and each partner entry are
    rewritten here, keyed by partner *name*.
    """
    agents = [safe_load(path.read_text()) or {} for path in agent_files]
    # name -> where that agent actually is
    located = {
        cfg.get("name", path.stem): dict(
            name=cfg.get("name", path.stem), host=host, port=port, protocol=protocol
        )
        for cfg, path, port in zip(agents, agent_files, agent_ports)
    }

    runner_out = out_dir / "runner.yml"
    runner_cfg = safe_load(runner_files[0].read_text()) if runner_files else {}
    runner_cfg = dict(runner_cfg or {})
    runner_cfg.setdefault("name", "runner")
    runner_cfg["port"] = runner_port
    runner_out.write_text(safe_dump(runner_cfg))

    agent_outs = []
    for cfg, path, port in zip(agents, agent_files, agent_ports):
        cfg = dict(cfg)
        cfg["host"] = host
        cfg["port"] = port
        cfg["protocol"] = protocol
        cfg["runner_host"] = host
        cfg["runner_port"] = runner_port
        cfg["runner_protocol"] = protocol
        for negotiation in (cfg.get("negotiations") or {}).values():
            negotiation["partners"] = [
                _relocate_partner(partner, located)
                for partner in negotiation.get("partners") or []
            ]
        out = out_dir / f"{path.stem}.yml"
        out.write_text(safe_dump(cfg))
        agent_outs.append(out)
    return runner_out, agent_outs


def _relocate_partner(partner: dict, located: dict[str, dict]) -> dict:
    """Point one partner entry at where that agent is actually running.

    Partners we launched are rewritten outright. For any other partner we at
    least normalise `address:` to `host:` -- `PartnerModel` only understands
    `host`, so an `address:` key was silently dropped and the host quietly
    defaulted to 127.0.0.1.
    """
    known = located.get(partner.get("name"))
    if known is not None:
        return dict(known)
    partner = dict(partner)
    if "host" not in partner and "address" in partner:
        partner["host"] = partner.pop("address")
    return partner


def _wait_ready(address: Address, timeout: float = 60.0) -> None:
    """Block until a service answers on ``address`` (any protocol)."""
    channel = channel_for(address)
    deadline = time() + timeout
    last: Exception | None = None
    while time() < deadline:
        try:
            if channel.call("root", {}, http_method="get"):
                return
        except Exception as e:
            last = e
            sleep(0.2)
    print(f"[red]{address} never became ready: {last!r}[/red]")
