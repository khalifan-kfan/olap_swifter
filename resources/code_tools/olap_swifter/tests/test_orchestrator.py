"""End-to-end test of MedicalOLAPOrchestrator on the AMR schema's dummy data."""

from pathlib import Path

import pytest

from olap_swifter.orchestrator import MedicalOLAPOrchestrator
from olap_swifter.schema.registry import SchemaRegistry


def test_full_orchestration_pipeline():
    orch = MedicalOLAPOrchestrator()
    stats = orch.ingest_synthetic_dataset(num_visits=100)  # the only registered schema
    assert stats["num_visits"] == 100 and stats["raw_records_generated"] > 0

    res = orch.run_full_pipeline()
    assert res["zero_variable_loss"] is True and res["missing_variables"] == []
    assert res["semantic_validation"]["total_tests"] > 0
    assert res["semantic_validation"]["failed"] == 0
    assert "fact_ast_results" not in res["join_analysis"]  # paths are measured from the main fact
    assert "dim_facility" in res["join_analysis"]
    assert res["deepest_slices_count"] > 0
    assert Path(res["reviewer_html"]).exists()


def test_default_schema_needs_a_single_registered_schema(tmp_path):
    empty = SchemaRegistry(tmp_path)
    with pytest.raises(KeyError, match="no schema registered"):
        empty.default_id()
    assert SchemaRegistry().default_id() == "amr_surveillance"
    assert SchemaRegistry().default_id("x") == "x"
