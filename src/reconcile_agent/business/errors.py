"""Stable, payload-free errors suitable for operator CLI output."""


class AdapterError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def require(condition, code: str, message: str) -> None:
    if not condition:
        raise AdapterError(code, message)
