import logging
import json
import sys

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": self.formatTime(record, self.datefmt),
            "module": record.module,
        }
        return json.dumps(log_entry)

def setup_logging(level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers = [handler]

    return logger
