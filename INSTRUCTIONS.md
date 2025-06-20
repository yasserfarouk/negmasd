the current example uses ports for the comms between the buyer seller and mechanism, 
I need to have 3 files that have a similar mechanism to the example.py in metaagents (on_recieve, on_create, on_destroy)
each file needs to be a buyer, seller and mechanism and do the same thing as the current example - I then need to have a utility function in each script
that acts as a mqtt integration, which will listen to a topic (name of the agent) and pass data to the agent and then the agent on_recieve will then give a result back to the mqtt function (along with the approprioate topic) to route to the right destination
place all these files in metaagents and test it runs the negotiation 