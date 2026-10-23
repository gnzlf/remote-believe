"""Keep HTTP failures distinct from an unavailable Office connection."""


class OfficeError(Exception):
    pass


class OfficeNetworkError(OfficeError, ConnectionError):
    pass


class OfficeResponseError(OfficeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status
