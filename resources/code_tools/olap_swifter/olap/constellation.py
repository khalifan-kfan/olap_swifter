"""
Medical OLAP Fact Constellation (Galaxy) Schema — Metadata Only.
The static DDL has been removed; tables are now created dynamically by
DynamicOLAPBuilder or inlined in MedicalOLAPStore._init_schemas().
"""

from typing import Dict, List, Any


def get_constellation_schema_metadata() -> Dict[str, Any]:
    """Returns metadata describing the constellation schema relationships and join keys."""
    return {
        "model_type": "constellation",
        "central_spine": "dim_visit",
        "spine_key": "visit_id",
        "dimensions": [
            "dim_visit", "dim_patient", "dim_facility", "dim_provider",
            "dim_date", "dim_diagnosis", "dim_medication", "dim_lab_test"
        ],
        "facts": [
            "fact_encounters", "fact_diagnoses", "fact_prescriptions",
            "fact_lab_results", "fact_vitals"
        ],
        "relationships": [
            # Spine connections
            {"from": "dim_visit", "to": "dim_patient", "key": "patient_id"},
            {"from": "dim_visit", "to": "dim_facility", "key": "facility_id"},
            {"from": "dim_visit", "to": "dim_provider", "key": "provider_id"},
            {"from": "dim_visit", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},

            # Fact connections to spine & dimensions
            {"from": "fact_encounters", "to": "dim_visit", "key": "visit_id"},
            {"from": "fact_encounters", "to": "dim_patient", "key": "patient_id"},
            {"from": "fact_encounters", "to": "dim_facility", "key": "facility_id"},
            {"from": "fact_encounters", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},

            {"from": "fact_diagnoses", "to": "dim_visit", "key": "visit_id"},
            {"from": "fact_diagnoses", "to": "dim_patient", "key": "patient_id"},
            {"from": "fact_diagnoses", "to": "dim_diagnosis", "key": "diagnosis_id"},
            {"from": "fact_diagnoses", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},

            {"from": "fact_prescriptions", "to": "dim_visit", "key": "visit_id"},
            {"from": "fact_prescriptions", "to": "dim_patient", "key": "patient_id"},
            {"from": "fact_prescriptions", "to": "dim_medication", "key": "medication_id"},
            {"from": "fact_prescriptions", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},

            {"from": "fact_lab_results", "to": "dim_visit", "key": "visit_id"},
            {"from": "fact_lab_results", "to": "dim_patient", "key": "patient_id"},
            {"from": "fact_lab_results", "to": "dim_lab_test", "key": "test_id"},
            {"from": "fact_lab_results", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},

            {"from": "fact_vitals", "to": "dim_visit", "key": "visit_id"},
            {"from": "fact_vitals", "to": "dim_patient", "key": "patient_id"},
            {"from": "fact_vitals", "to": "dim_date", "from_key": "visit_date", "to_key": "date_id"},
        ]
    }
