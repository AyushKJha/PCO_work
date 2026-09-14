from __future__ import annotations


from apps.api.db import create_session_factory, create_tables
from apps.api.models import (
    EvalCase,
    EvalDataset,
    EvalGrader,
    OptimizationResult,
    Organization,
    OutboxEvent,
    Project,
)


def test_gate1_persistence_models_store_eval_outbox_and_result_metadata(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'gate1.db'}")
    create_tables(factory)

    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        project = Project(name="Support", slug="support", organization_id=organization.id)
        session.add(project)
        session.flush()
        dataset = EvalDataset(
            organization_id=organization.id,
            project_id=project.id,
            name="smoke",
            version=1,
            metadata_json={"source": "test"},
        )
        session.add(dataset)
        session.flush()
        session.add(
            EvalCase(
                dataset_id=dataset.id,
                case_id="case-1",
                input_data={"prompt": "hello"},
                expected={"answer": "hi"},
                metadata_json={},
                ordinal=0,
            )
        )
        session.add(
            EvalGrader(
                dataset_id=dataset.id,
                name="exact",
                kind="exact_match",
                config={"field": "answer"},
                ordinal=0,
            )
        )
        session.add(
            OutboxEvent(
                organization_id=organization.id,
                aggregate_type="optimization",
                aggregate_id="job-1",
                event_type="optimization.queued",
                dedupe_key="job-1:queued",
                payload={"job_id": "job-1"},
            )
        )
        session.add(
            OptimizationResult(
                organization_id=organization.id,
                project_id=project.id,
                job_id="job-1",
                status="completed",
                recommendation={"id": "fast"},
                metadata_json={"schema_version": "v1"},
            )
        )

    with factory() as session:
        stored = session.query(EvalDataset).one()
        assert stored.cases[0].case_id == "case-1"
        assert stored.graders[0].kind == "exact_match"
        assert session.query(OutboxEvent).one().status == "pending"
        assert session.query(OptimizationResult).one().recommendation == {"id": "fast"}

