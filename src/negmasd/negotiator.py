from negmas import SAONegotiator, SAOResponse, SAOState


class SAORemoteNegotiator(SAONegotiator):
    def __call__(self, state: SAOState, dest: str | None = None) -> SAOResponse:
        raise NotImplementedError("SAORemoteNegotiator is not implemented")
