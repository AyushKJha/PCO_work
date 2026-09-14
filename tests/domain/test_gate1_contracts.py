from __future__ import annotations

import pytest

from services.contracts import (
    CONTRACT_VERSION,
    CandidateConfig,
    JobPayload,
    OptimizationConfig,
)


def test_candidate_config_is_versioned_and_round_trips_wire_data() -> None:
    candidate = CandidateConfig(
        id="fast",
        provider="openai",
        model="gpt-4o-mini",
        parameters={"temperature": 0},
        cost_usd=0.01,
    )

    wire = candidate.model_dump(mode="json")
    assert wire["schema_version"] == CONTRACT_VERSION
    assert CandidateConfig.model_validate(wire) == candidate


def test_optimization_config_rejects_invalid_search_limits() -> None:
    with pytest.raises(ValueError, match="beam_width"):
        OptimizationConfig(candidates=[{"id": "a"}], beam_width=0)


def test_job_payload_normalizes_legacy_nested_candidates() -> None:
    payload = JobPayload.model_validate(
        {
            "kind": "optimization",
            "dataset_id": "dataset-1",
            "config": {
                "candidates": [{"id": "a", "cost_usd": 0.2}],
                "beam_width": 2,
            },
        }
    )

    assert payload.schema_version == CONTRACT_VERSION
    assert [candidate.id for candidate in payload.candidates] == ["a"]
    assert payload.optimization.beam_width == 2

