from negmas.preferences import BaseUtilityFunction


class RemoteUtilityFunction(BaseUtilityFunction):
    """
    A utility function that is used to represent a remote utility function.
    It is used to represent a utility function that is not available locally.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._remote = True

    def __call__(self, *args, **kwargs):
        raise NotImplementedError("Remote utility functions cannot be called directly.")
