from fastapi import FastAPI

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/load-config")
async def load_config():
    return {"message": "Hello World"}


@app.get("/start-negotiation")
async def start_negotiation():
    return {"message": "Hello World"}


@app.get("/request-negotiation")
async def request_negotiation():
    return {"message": "Hello World"}


@app.get("/neginfo")
async def neginfo():
    return {"message": "Hello World"}


@app.get("/receive-results")
async def receive_results():
    return {"message": "Hello World"}
