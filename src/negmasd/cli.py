from pathlib import Path
import multiprocessing
from time import sleep
import typer
import uvicorn
from rich import print

from negmasd.common import get_response, SUCCESS
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
    resp = get_response(
        f"http://{host}:{port}/load-config",
        data=StrModel(value=str(config)),
        response_type=ResponseModel,
    )
    print(resp)


@app.command()
def runner(port: int = 9_000):
    """
    Start the session runner server. You must have one of these running.
    """
    print(f"running runner server on port {port}")
    base = "negmasd.runner:app"
    uvicorn.run(base, host="0.0.0.0", port=port)


@app.command()
def builtin(app: str, base_port: int = 8_000, runner_port: int = 9_000):
    """
    Start the session runner server. You must have one of these running. Pass --runner-port=0 to avoid starting a new runner (if one already exists).
    """
    print(f"running builtin app {app}")
    app_path = Path(__file__).parent / "apps" / app / "agents"
    agent_files = list(app_path.glob("*.yml")) + list(app_path.glob("*.yaml"))
    if not agent_files:
        print(f"No agent files found in {app_path}")
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
    for i, path in enumerate(agent_files):
        port = base_port + i + 1
        config = path.absolute()

        print(f"running agent server on port {port} with config from {config}")
        base = "negmasd.agent:app"
        p = multiprocessing.Process(
            target=uvicorn.run, args=(base,), kwargs=dict(host="0.0.0.0", port=port)
        )
        p.start()
        processes.append(p)
        sleep(2)
        resp = get_response(
            f"http://{host}:{port}/load-config",
            data=StrModel(value=str(config)),
            response_type=ResponseModel,
        )
        if resp.status != SUCCESS:
            print(f"Failed to run agent on port {port} with config: {config}")
            print(resp)
            break
    for p in processes:
        p.join()
