import dataclasses
import json


class KalanjiyamJSONEncoder(json.JSONEncoder):
    """Extend Flask's default encoder to support dataclasses."""

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)
