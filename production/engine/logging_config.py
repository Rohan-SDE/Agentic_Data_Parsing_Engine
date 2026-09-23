import json
import logging
import time


class JsonFormatter(logging.Formatter):
    def format(self, record):
        item = {"time": time.time(), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for key in ("request_id", "job_id", "status", "method", "duration_ms"):
            if hasattr(record, key):
                item[key] = getattr(record, key)
        return json.dumps(item)


def configure():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
