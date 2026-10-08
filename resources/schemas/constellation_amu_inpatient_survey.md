# Warehouse Constellation

Dataset **amu_inpatient_survey** feeds 31 columns across 5 tables (marked ★).

```mermaid
graph LR
    dim_visit["dim_visit ★"]
    dim_antibiotic["dim_antibiotic"]
    dim_diagnosis["dim_diagnosis"]
    dim_facility["dim_facility ★"]
    dim_lab_test["dim_lab_test"]
    dim_medication["dim_medication ★"]
    dim_organism["dim_organism"]
    dim_patient["dim_patient ★"]
    fact_ast_results(["fact_ast_results"])
    fact_diagnoses(["fact_diagnoses"])
    fact_encounters(["fact_encounters"])
    fact_lab_results(["fact_lab_results"])
    fact_prescriptions(["fact_prescriptions ★"])
    dim_visit -->|facility_id| dim_facility
    dim_visit -->|patient_id| dim_patient
    fact_ast_results -->|tested_antibiotic| dim_antibiotic
    fact_ast_results -->|organism_name| dim_organism
    fact_ast_results -->|visit_id| dim_visit
    fact_diagnoses -->|diagnosis_id| dim_diagnosis
    fact_diagnoses -->|visit_id| dim_visit
    fact_encounters -->|visit_id| dim_visit
    fact_lab_results -->|test_id| dim_lab_test
    fact_lab_results -->|visit_id| dim_visit
    fact_prescriptions -->|medication_id| dim_medication
    fact_prescriptions -->|visit_id| dim_visit
    classDef hl stroke-width:3px,stroke:#d29922
    class dim_visit,dim_facility,dim_medication,dim_patient,fact_prescriptions hl
```

## dim_visit ★

spine; used by amr_surveillance, clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
| ★ | **visit_id** | VARCHAR | key | amu_inpatient_survey |
| ★ | **patient_id** | VARCHAR | link | amu_inpatient_survey |
| ★ | **facility_id** | VARCHAR | link | amu_inpatient_survey |
| ★ | **visit_date** | DATE | attribute | amu_inpatient_survey |
| ★ | **admission_ward** | VARCHAR | attribute | amu_inpatient_survey |
|  | age_group | VARCHAR | attribute |  |
|  | visit_type | VARCHAR | attribute |  |
| ★ | **ward_type** | VARCHAR | attribute | amu_inpatient_survey |
|  | triage_urgency | VARCHAR | attribute |  |
| ★ | **age_years** | BIGINT | attribute | amu_inpatient_survey |
| ★ | **survey_date** | DATE | attribute | amu_inpatient_survey |

## dim_antibiotic

dimension; used by amr_surveillance

| | column | type | role | datasets |
|---|---|---|---|---|
|  | tested_antibiotic | VARCHAR | key |  |
|  | antibiotic_class | VARCHAR | attribute |  |

## dim_diagnosis

dimension; used by clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
|  | diagnosis_id | VARCHAR | key |  |
|  | diagnosis_name | VARCHAR | attribute |  |
|  | icd10_code | VARCHAR | attribute |  |
|  | clinical_category | VARCHAR | attribute |  |

## dim_facility ★

dimension; used by amr_surveillance, clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
| ★ | **facility_id** | VARCHAR | key | amu_inpatient_survey |
| ★ | **facility_name** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **facility_level** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **district** | VARCHAR | attribute | amu_inpatient_survey |
|  | region | VARCHAR | attribute |  |

## dim_lab_test

dimension; used by clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
|  | test_id | VARCHAR | key |  |
|  | test_name | VARCHAR | attribute |  |

## dim_medication ★

dimension; used by clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
| ★ | **medication_id** | VARCHAR | key | amu_inpatient_survey |
| ★ | **medication_name** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **antibiotic_class** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **aware_category** | VARCHAR | attribute | amu_inpatient_survey |

## dim_organism

dimension; used by amr_surveillance

| | column | type | role | datasets |
|---|---|---|---|---|
|  | organism_name | VARCHAR | key |  |

## dim_patient ★

dimension; used by amr_surveillance, clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
| ★ | **patient_id** | VARCHAR | key | amu_inpatient_survey |
| ★ | **gender** | VARCHAR | attribute | amu_inpatient_survey |
|  | location_district | VARCHAR | attribute |  |

## fact_ast_results

fact; used by amr_surveillance — main fact of amr_surveillance

| | column | type | role | datasets |
|---|---|---|---|---|
|  | visit_id | VARCHAR | link |  |
|  | organism_name | VARCHAR | link |  |
|  | tested_antibiotic | VARCHAR | link |  |
|  | specimen_type | VARCHAR | attribute |  |
|  | mic_ug_ml | DOUBLE | measure |  |
|  | zone_diameter_mm | DOUBLE | measure |  |
|  | ast_interpretation | VARCHAR | attribute |  |
|  | is_mdr_isolate | BOOLEAN | attribute |  |

## fact_diagnoses

fact; used by clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
|  | visit_id | VARCHAR | link | amu_inpatient_survey |
|  | diagnosis_id | VARCHAR | link |  |

## fact_encounters

fact; used by amr_surveillance, clinical_visit — main fact of clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
|  | visit_id | VARCHAR | link | amu_inpatient_survey |
|  | length_of_stay_days | DOUBLE | measure |  |
|  | encounter_cost_ugx | DOUBLE | measure |  |
|  | prior_antibiotic_use_30d | VARCHAR | attribute |  |
|  | substance_use_history | VARCHAR | attribute |  |

## fact_lab_results

fact; used by clinical_visit

| | column | type | role | datasets |
|---|---|---|---|---|
|  | visit_id | VARCHAR | link | amu_inpatient_survey |
|  | test_id | VARCHAR | link |  |
|  | ast_interpretation | VARCHAR | attribute |  |
|  | zone_diameter_mm | DOUBLE | measure |  |

## fact_prescriptions ★

fact; used by clinical_visit — main fact of dataset amu_inpatient_survey

| | column | type | role | datasets |
|---|---|---|---|---|
| ★ | **visit_id** | VARCHAR | link | amu_inpatient_survey |
| ★ | **medication_id** | VARCHAR | link | amu_inpatient_survey |
|  | drug_cost_ugx | DOUBLE | measure |  |
| ★ | **drug_administration_route** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **diagnosis_icd10** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **dose_mg** | DOUBLE | measure | amu_inpatient_survey |
| ★ | **doses_per_day** | BIGINT | measure | amu_inpatient_survey |
| ★ | **treatment_days** | BIGINT | measure | amu_inpatient_survey |
| ★ | **indication_type** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **treatment_basis** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **culture_sent** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **specimen_type** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **organism_isolated** | VARCHAR | attribute | amu_inpatient_survey |
| ★ | **resistant_to_prescribed** | VARCHAR | attribute | amu_inpatient_survey |
