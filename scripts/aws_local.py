"""AWS V1 local simulations; these functions never call AWS."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

@dataclass
class SimulatedJob:
    job_id: str
    attempts: int = 0
    completed_candidates: set[str] | None = None
    def __post_init__(self): self.completed_candidates = self.completed_candidates or set()

def process_candidate(job: SimulatedJob, candidate_id: str, cost_usd: float, spent_usd: float, cap_usd: float) -> tuple[bool, float]:
    if candidate_id in job.completed_candidates: return False, spent_usd
    if cost_usd < 0 or spent_usd + cost_usd > cap_usd: raise RuntimeError("spend cap exceeded")
    job.completed_candidates.add(candidate_id); return True, spent_usd + cost_usd

def load_span_count(count: int) -> dict[str, int | bool]:
    if count < 0: raise ValueError("count must be non-negative")
    return {"accepted": count, "aws_calls": 0, "corruption": False}

def delete_artifacts(keys: Iterable[str], existing: set[str]) -> set[str]:
    return existing.difference(set(keys))

def readiness(database_ok: bool) -> bool: return bool(database_ok)
