import logging


class MaxLevelFilter(logging.Filter):
    def __init__(self, level: int | str):
        super().__init__()
        if isinstance(level, str):
            self.level = logging._nameToLevel.get(level.upper(), logging.WARNING)
        else:
            self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level


class HealthcheckAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = getattr(record, "args", ())
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2])
            if path in {"/healthz", "/readyz", "/favicon.ico"}:
                return False
        message = record.getMessage()
        return (
            '"GET /healthz HTTP/' not in message
            and '"GET /readyz HTTP/' not in message
            and '"GET /favicon.ico HTTP/' not in message
        )
