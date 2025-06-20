from pathlib import Path
import multiprocessing
import asyncio
from time import sleep
import typer
import uvicorn
from rich import print

from negmasd.common import (
    get_response,
    SUCCESS,
    RUNNER_DEFAULT_PORT,
    AGENT_BASE_PORT,
)
from negmasd.models import ResponseModel, StrModel

app = typer.Typer()


@app.command()
def agent(port: int, config: Path, host="127.0.0.1"):
    """
    Start the agent server.
    """
    print(f"running agent server on port {port} with config from {config}")
    base = "negmasd.agent:app"
    uvicorn.run(base, host="0.0.0.0", port=port)
    sleep(2)
    loop = asyncio.get_event_loop()
    resp = loop.run_until_complete(
        get_response(
            f"http://{host}:{port}/load-config",
            data=StrModel(value=str(config)),
            response_type=ResponseModel,
        )
    )
    print(resp)


@app.command()
def runner(port: int = RUNNER_DEFAULT_PORT):
    """
    Start the session runner server. You must have one of these running.
    """
    print(f"running runner server on port {port}")
    base = "negmasd.runner:app"
    uvicorn.run(base, host="0.0.0.0", port=port)


@app.command()
def builtin(
    app: str,
    base_port: int = AGENT_BASE_PORT,
    runner_port: int = RUNNER_DEFAULT_PORT,
    load_configs: bool = True,
):
    """
    Start the session runner server. You must have one of these running. Pass --runner-port=0 to avoid starting a new runner (if one already exists).
    """
    print(f"running builtin app {app}")
    runner_path = Path(__file__).parent / "apps" / app
    runner_files = list(runner_path.glob("runner.yml")) + list(
        runner_path.glob("runner.yaml")
    )
    agents_path = Path(__file__).parent / "apps" / app / "agents"
    agent_files = list(agents_path.glob("*.yml")) + list(agents_path.glob("*.yaml"))
    if not agent_files:
        print(f"No agent files found in {agents_path}")
        return
    print(f"Will use config files:\n\t{'\n\t'.join([str(_) for _ in agent_files])}")
    processes = []
    if runner_port:
        port = runner_port
        print(f"running runner server on port {port}")
        base = "negmasd.runner:app"
        p = multiprocessing.Process(
            target=uvicorn.run, args=(base,), kwargs=dict(host="0.0.0.0", port=port)
        )
        p.start()
        processes.append(p)
    host = "127.0.0.1"
    for i in range(len(agent_files)):
        port = base_port + i + 1

        print(f"running agent server on port {port}.")
        base = "negmasd.agent:app"
        p = multiprocessing.Process(
            target=uvicorn.run, args=(base,), kwargs=dict(host="0.0.0.0", port=port)
        )
        p.start()
        processes.append(p)
    if load_configs:
        sleep(3)
        for path in runner_files:
            port = runner_port
            config = path.absolute()

            print(f"Loading config to runner on port {port} from {config}")
            base = "negmasd.runner:app"

            loop = asyncio.get_event_loop()
            resp = loop.run_until_complete(
                get_response(
                    f"http://{host}:{port}/load-config",
                    method="get",
                    data=dict(path=str(config)),
                    response_type=ResponseModel,
                )
            )
            if resp.status != SUCCESS:
                print(
                    f"Failed to load config for runner on port {port} from config: {config}"
                )
                print(resp)
        for i, path in enumerate(agent_files):
            port = base_port + i + 1
            config = path.absolute()

            print(f"Loading config to agent on port {port} from {config}")
            base = "negmasd.agent:app"

            loop = asyncio.get_event_loop()
            resp = loop.run_until_complete(
                get_response(
                    f"http://{host}:{port}/load-config",
                    method="get",
                    data=dict(path=str(config)),
                    response_type=ResponseModel,
                )
            )
            if resp.status != SUCCESS:
                print(f"Failed to load config for agent on port {port} from {config}")
                print(resp)
    else:
        sleep(1)
        print(
            f"Skipping loading configs. You can load them manually by calling http://{host}:{base_port + 1}/load-config with the path to the config file."
        )

    for p in processes:
        p.join()
