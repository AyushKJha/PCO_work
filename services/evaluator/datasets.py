"""Small, serializable evaluation dataset contract."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class EvalExample:
    id: str
    input: str
    expected: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalDataset:
    name: str
    examples: tuple[EvalExample, ...]

    def __init__(self, name: str, examples: Iterable[EvalExample] = ()) -> None:
        if not name:
            raise ValueError("dataset name is required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "examples", tuple(examples))

    @classmethod
    def from_jsonl(cls, path: str | Path, name: str | None = None) -> "EvalDataset":
        rows = []
        for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                rows.append(EvalExample(str(row["id"]), str(row.get("input", row.get("prompt", ""))), row["expected"], row.get("metadata", {})))
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid dataset row {line_number}") from exc
        return cls(name or Path(path).stem, rows)
