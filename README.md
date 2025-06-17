# A simple wrapper around negmas

The main implementation files are the following:

- `agent.py` Implements an agent that can engage in negotiation
- `runner.py` Implements a service that can run a negotiation locally based on information provided by agents and log/report results.
- `cli.py` Implements a simple CLI to start agents; runners and apps

An app is defined by a set of agent configs and (optionally) a runner config. For an example, see `src/negamasd/procurement`

You can see the available functionality of the CLI using:

```bash
negmasd --help

```

You can try a simple scenario using:

```bash
negmasd builtin procurement
```

This will start the following FastAPI processes:

1. runner at port 9010
1. buyer at port 8001
1. seller at port 8002

It should also call `/load-config` for all of them to load the corresponding configuration file from  `src/negamasd/procurement`.

You can then navigate to [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)

You can then use this docs interface to run `/start-negotiation` giving it the value `trade` for the `neg` parameter.

This will start a negotiation. Upon completion the results of the negotiation will be available to the seller, the buyer and the runner.

You can check that by running `/peak` from the same page which will show you the internal memory of the buyer including the result of this negotiation.

You can then navigate to [http://127.0.0.1:9010/docs](http://127.0.0.1:9010/docs) and run `/peak` to see the complete history of this negotiation among other data.



