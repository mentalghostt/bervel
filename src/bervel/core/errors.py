class CoreError(Exception):
    """Expected application failure, safe to present at the HTTP boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f'{code}: {message}')
