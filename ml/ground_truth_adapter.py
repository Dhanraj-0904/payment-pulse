"""Read Phase 2 ground truth only for separate evaluation calls."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvaluationGroundTruth:
    incident_id: str
    incident_type: str
    start_time: datetime
    end_time: datetime
    affected_dimensions: dict[str, str | None]


def load_ground_truth(path: str | Path) -> list[EvaluationGroundTruth]:
    with Path(path).open(encoding="utf-8") as stream:
        rows = json.load(stream)
    return [
        EvaluationGroundTruth(
            incident_id=row["incident_id"],
            incident_type=row["incident_type"],
            start_time=datetime.fromisoformat(row["start_time"]),
            end_time=datetime.fromisoformat(row["end_time"]),
            affected_dimensions=row["affected_dimensions"],
        )
        for row in rows
    ]
