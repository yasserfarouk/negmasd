import typer

app = typer.Typer()


@app.command()
def agent(port: int = 8001):
    """
    Start the agent server.
    """
    print(f"running agent server on port {port}")


@app.command()
def manager(port: int = 9000):
    """
    Start the session manger server. You must have one of these running.
    """
    print(f"running manager server on port {port}")


@app.command()
def runner(port: int = 10_000):
    """
    Start the session runner server. You must have one of these running.
    """
    print(f"running runner server on port {port}")
