"""
End-to-end integration test for MedicalOLAPOrchestrator.
"""

import pytest
from olap_swifter.orchestrator import MedicalOLAPOrchestrator
from pathlib import Path


def test_full_orchestration_pipeline():
    orch = MedicalOLAPOrchestrator()
    data_stats = orch.ingest_synthetic_dataset(num_visits=100)

    assert data_stats["num_visits"] == 100
    assert data_stats["raw_records_generated"] > 0

    res = orch.run_full_pipeline()

    # 1. Zero variable loss
    assert res["zero_variable_loss"] is True
    assert len(res["missing_variables"]) == 0
    assert res["total_variables"] > 10

    # 2. Semantic validation
    assert res["semantic_validation"]["total_tests"] > 0

    # 3. Join path analysis
    assert "fact_encounters" not in res["join_analysis"]
    assert "dim_facility" in res["join_analysis"]

    # 4. Deepest cube slices
    assert res["deepest_slices_count"] > 0

    # 5. Interactive reviewer HTML
    assert Path(res["reviewer_html"]).exists()
