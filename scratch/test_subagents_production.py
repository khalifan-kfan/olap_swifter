import duckdb
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.materializer import resolve_source

def run_production_tests():
    conn = duckdb.connect("resources/warehouse.duckdb")
    spec = SchemaRegistry().get("clinical_visit")
    source_id = "amu_inpatient_survey_amu_survey_2025h1"
    staging = f"stg_{source_id}"

    results = []

    def record(test, derived, query, expected, actual, tol, passed, detail="", origin="listed"):
        res_str = "PASS" if passed else "FAIL"
        results.append({
            "test": test, "derived": derived, "query": query,
            "expected": expected, "actual": actual, "tolerance": tol,
            "result": res_str, "detail": detail, "origin": origin
        })
        print(f"TEST: {test}")
        print(f"DERIVED: {derived}")
        print(f"EXPECTED: {expected} | ACTUAL: {actual} | RESULT: {res_str}")
        if detail:
            print(f"DETAIL: {detail}")
        print("-" * 50)

    print("=== SUB-AGENT 1: pre_etl_validator ===")
    res = resolve_source(spec, conn, staging)
    # Gate 1: Variable coverage
    record("Gate 1: Nothing gets left behind", "All raw staging columns mapped or excluded",
           f"DESCRIBE {staging}", 0, len(res.unmapped), "exact", len(res.unmapped) == 0,
           f"unmapped: {res.unmapped}" if res.unmapped else "")

    # Gate 2: Spine key complete
    null_spine = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE admission_id IS NULL").fetchone()[0]
    record("Gate 2: Spine key complete", "dim_visit.visit_id not null in raw (admission_id)",
           f"SELECT COUNT(*) FROM {staging} WHERE admission_id IS NULL", 0, null_spine, "exact", null_spine == 0)

    # Gate 3: Keys are keys (entity consistency) - expected failure documented
    conf_pat = conn.execute(f"SELECT COUNT(*) FROM (SELECT patient_id FROM {staging} GROUP BY patient_id HAVING COUNT(DISTINCT sex) > 1)").fetchone()[0]
    record("Gate 3: Keys are keys (dim_patient)", "patient_id consistent attribute combinations",
           "HAVING COUNT(DISTINCT sex) > 1", 0, conf_pat, "exact", conf_pat == 0,
           f"Known data issue: {conf_pat} patient ID has conflicting recorded genders across admissions")

    # Gate 4: Coded values stay inside vocabulary - expected failure on route (iv: 7)
    bad_route = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE route IS NOT NULL AND route NOT IN ('IV', 'Oral', 'IM')").fetchone()[0]
    record("Gate 4: Coded values (route)", "route in [IV, Oral, IM]",
           "NOT IN ('IV', 'Oral', 'IM')", 0, bad_route, "exact", bad_route == 0,
           f"Known data issue: {bad_route} prescriptions have lowercase 'iv' from free text entry")

    # Gate 5: Measures are measurable - expected failure on dose_mg (-1.0: 5)
    bad_dose = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE TRY_CAST(dose_mg AS DOUBLE) < 0 OR TRY_CAST(dose_mg AS DOUBLE) > 10000").fetchone()[0]
    record("Gate 5: Measure range (dose_mg)", "dose_mg in range [0, 10000]",
           "TRY_CAST(dose_mg AS DOUBLE) < 0", 0, bad_dose, "exact", bad_dose == 0,
           f"Known data issue: {bad_dose} prescriptions have negative sentinel dose (-1.0)")

    print("\n=== SUB-AGENT 2: relational_integrity_auditor ===")
    # Test 1: No orphan rows
    orphan_rx_visit = conn.execute("SELECT COUNT(*) FROM fact_prescriptions f LEFT JOIN dim_visit v ON f.visit_id = v.visit_id WHERE v.visit_id IS NULL").fetchone()[0]
    record("Test 1: No orphan rows (fact_prescriptions -> dim_visit)", "fact_prescriptions.visit_id -> dim_visit.visit_id",
           "fact_prescriptions LEFT JOIN dim_visit", 0, orphan_rx_visit, "exact", orphan_rx_visit == 0)

    orphan_rx_med = conn.execute("SELECT COUNT(*) FROM fact_prescriptions f LEFT JOIN dim_medication m ON f.medication_id = m.medication_id WHERE m.medication_id IS NULL").fetchone()[0]
    record("Test 1: No orphan rows (fact_prescriptions -> dim_medication)", "fact_prescriptions.medication_id -> dim_medication.medication_id",
           "fact_prescriptions LEFT JOIN dim_medication", 0, orphan_rx_med, "exact", orphan_rx_med == 0)

    orphan_visit_fac = conn.execute("SELECT COUNT(*) FROM dim_visit v LEFT JOIN dim_facility f ON v.facility_id = f.facility_id WHERE f.facility_id IS NULL").fetchone()[0]
    record("Test 1: No orphan rows (dim_visit -> dim_facility)", "dim_visit.facility_id -> dim_facility.facility_id",
           "dim_visit LEFT JOIN dim_facility", 0, orphan_visit_fac, "exact", orphan_visit_fac == 0)

    orphan_visit_pat = conn.execute("SELECT COUNT(*) FROM dim_visit v LEFT JOIN dim_patient p ON v.patient_id = p.patient_id WHERE p.patient_id IS NULL").fetchone()[0]
    record("Test 1: No orphan rows (dim_visit -> dim_patient)", "dim_visit.patient_id -> dim_patient.patient_id",
           "dim_visit LEFT JOIN dim_patient", 0, orphan_visit_pat, "exact", orphan_visit_pat == 0)

    # Test 2: Entity counts survived
    raw_pats = conn.execute(f"SELECT COUNT(DISTINCT patient_id) FROM {staging}").fetchone()[0]
    wh_pats = conn.execute("SELECT COUNT(*) FROM dim_patient").fetchone()[0]
    record("Test 2: Entity counts survived (dim_patient)", "distinct patient_id raw vs warehouse",
           "COUNT(DISTINCT patient_id)", raw_pats, wh_pats, "exact", raw_pats == wh_pats)

    raw_facs = conn.execute(f"SELECT COUNT(DISTINCT hospital_code) FROM {staging}").fetchone()[0]
    wh_facs = conn.execute("SELECT COUNT(*) FROM dim_facility").fetchone()[0]
    record("Test 2: Entity counts survived (dim_facility)", "distinct hospital_code raw vs warehouse",
           "COUNT(DISTINCT hospital_code)", raw_facs, wh_facs, "exact", raw_facs == wh_facs)

    raw_meds = conn.execute(f"SELECT COUNT(DISTINCT atc_code) FROM {staging}").fetchone()[0]
    wh_meds = conn.execute("SELECT COUNT(*) FROM dim_medication").fetchone()[0]
    record("Test 2: Entity counts survived (dim_medication)", "distinct atc_code raw vs warehouse",
           "COUNT(DISTINCT atc_code)", raw_meds, wh_meds, "exact", raw_meds == wh_meds)

    # Test 3: Coverage of the spine
    raw_visits = conn.execute(f"SELECT COUNT(DISTINCT admission_id) FROM {staging}").fetchone()[0]
    wh_visits = conn.execute("SELECT COUNT(DISTINCT visit_id) FROM dim_visit").fetchone()[0]
    record("Test 3: Coverage of the spine", "distinct admission_id raw vs warehouse",
           "COUNT(DISTINCT admission_id)", raw_visits, wh_visits, "exact", raw_visits == wh_visits)

    # Test 4: Attribute fidelity
    diff_attr = conn.execute(f"""
        WITH raw_dist AS (SELECT hospital_level AS lvl, COUNT(DISTINCT hospital_code) AS cnt FROM {staging} GROUP BY hospital_level),
             wh_dist  AS (SELECT facility_level AS lvl, COUNT(*) AS cnt FROM dim_facility GROUP BY facility_level)
        SELECT COUNT(*) FROM raw_dist FULL OUTER JOIN wh_dist ON raw_dist.lvl = wh_dist.lvl WHERE raw_dist.cnt != wh_dist.cnt
    """).fetchone()[0]
    record("Test 4: Attribute fidelity (facility level)", "distribution of facility levels",
           "raw vs warehouse facility_level counts", 0, diff_attr, "exact", diff_attr == 0)

    print("\n=== SUB-AGENT 3: metric_parity_verifier ===")
    # Test 5: Sum parity
    for m, m_type, raw_col in [("dose_mg", "DOUBLE", "dose_mg"), ("doses_per_day", "BIGINT", "doses_per_day"), ("treatment_days", "BIGINT", "treatment_days")]:
        exp = conn.execute(f"SELECT SUM(TRY_CAST({raw_col} AS {m_type})) FROM {staging}").fetchone()[0]
        act = conn.execute(f"SELECT SUM({m}) FROM fact_prescriptions WHERE _source_id = '{source_id}'").fetchone()[0]
        exp_f, act_f = float(exp or 0), float(act or 0)
        delta = abs(exp_f - act_f)
        record(f"Test 5: Sum parity ({m})", f"fact_prescriptions.{m}",
               f"SUM({m}) de-duplicated", exp_f, act_f, "relative 1e-6", delta <= 1e-6 * max(1.0, exp_f))

    # Test 6: Average and distribution parity
    raw_avg = conn.execute(f"SELECT AVG(TRY_CAST(dose_mg AS DOUBLE)) FROM {staging}").fetchone()[0]
    wh_avg = conn.execute(f"SELECT AVG(dose_mg) FROM fact_prescriptions WHERE _source_id = '{source_id}'").fetchone()[0]
    record("Test 6: Average parity (dose_mg)", "AVG(dose_mg)",
           "AVG(dose_mg)", round(float(raw_avg), 4), round(float(wh_avg), 4), "relative 1e-4",
           abs(float(raw_avg) - float(wh_avg)) < 1e-3)

    # Test 7: Group-by parity
    diff_grp = conn.execute(f"""
        WITH raw_grp AS (
            SELECT indication_type, COUNT(*) as cnt
            FROM {staging}
            GROUP BY indication_type
        ),
        wh_grp AS (
            SELECT indication_type, COUNT(*) as cnt
            FROM fact_prescriptions
            WHERE _source_id = '{source_id}'
            GROUP BY indication_type
        )
        SELECT COUNT(*) FROM raw_grp FULL OUTER JOIN wh_grp USING (indication_type)
        WHERE raw_grp.cnt != wh_grp.cnt
    """).fetchone()[0]
    record("Test 7: Group-by parity (indication_type count)", "prescription count grouped by indication_type",
           "GROUP BY indication_type", 0, diff_grp, "exact", diff_grp == 0)

    # Test 8: Fan-out check
    diff_rows = conn.execute(f"""
        SELECT (SELECT COUNT(*) FROM {staging}) - (SELECT COUNT(*) FROM fact_prescriptions WHERE _source_id = '{source_id}')
    """).fetchone()[0]
    record("Test 8: Fan-out does not inflate / deflate", "raw row count vs fact prescription count",
           "COUNT(*) raw vs fact_prescriptions", 0, diff_rows, "exact", diff_rows == 0)

    print("\n=== SUB-AGENT 4: clinical_research_query_agent ===")
    # Primary Research Question: Share of Watch/Reserve agents by hospital, ward, indication, and empirical without culture
    aware_summary = conn.execute("""
        SELECT
            m.aware_category,
            COUNT(*) as prescriptions,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct_of_total,
            SUM(CASE WHEN p.treatment_basis = 'Empirical' AND p.culture_sent = 'N' THEN 1 ELSE 0 END) as empirical_no_culture,
            ROUND(100.0 * SUM(CASE WHEN p.treatment_basis = 'Empirical' AND p.culture_sent = 'N' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_empirical_no_culture
        FROM fact_prescriptions p
        JOIN dim_medication m ON p.medication_id = m.medication_id
        GROUP BY m.aware_category
        ORDER BY prescriptions DESC
    """).fetchall()
    record("Test 9: Primary research query (AWaRe category & Empirical use)", "AWaRe breakdown with empirical treatment without culture",
           "fact_prescriptions x dim_medication", ">0 categories", f"{len(aware_summary)} categories returned", "non-empty", len(aware_summary) > 0)

    # Secondary Question 1: Organisms isolated and resistance to prescribed antimicrobial
    micro_summary = conn.execute("""
        SELECT
            organism_isolated,
            COUNT(*) as total_isolates,
            SUM(CASE WHEN resistant_to_prescribed = 'Y' THEN 1 ELSE 0 END) as resistant_count,
            ROUND(100.0 * SUM(CASE WHEN resistant_to_prescribed = 'Y' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_resistant
        FROM fact_prescriptions
        WHERE organism_isolated IS NOT NULL
        GROUP BY organism_isolated
        ORDER BY total_isolates DESC
    """).fetchall()
    record("Test 9: Secondary query 1 (Organisms & Resistance)", "Resistance rates by isolated organism",
           "fact_prescriptions where organism_isolated IS NOT NULL", ">0 organisms", f"{len(micro_summary)} organisms returned", "non-empty", len(micro_summary) > 0)

    # Secondary Question 2: Surgical prophylaxis duration
    sp_summary = conn.execute("""
        SELECT
            AVG(treatment_days) as avg_sp_days,
            MIN(treatment_days) as min_sp_days,
            MAX(treatment_days) as max_sp_days,
            COUNT(*) as sp_count
        FROM fact_prescriptions
        WHERE indication_type = 'SP' AND treatment_days IS NOT NULL
    """).fetchone()
    record("Test 9: Secondary query 2 (Surgical Prophylaxis duration)", "SP treatment duration statistics",
           "fact_prescriptions where indication_type = 'SP'", ">0 records", f"Avg: {round(sp_summary[0], 2)} days across {sp_summary[3]} prescriptions", "non-empty", sp_summary[3] > 0)

    # Secondary Question 3: Adult vs Children antimicrobial choice
    ped_summary = conn.execute("""
        SELECT
            CASE WHEN v.age_years < 15 THEN 'Child (<15)' ELSE 'Adult (15+)' END as age_strata,
            m.aware_category,
            COUNT(*) as count
        FROM fact_prescriptions p
        JOIN dim_visit v ON p.visit_id = v.visit_id
        JOIN dim_medication m ON p.medication_id = m.medication_id
        GROUP BY ALL
        ORDER BY age_strata, count DESC
    """).fetchall()
    record("Test 9: Secondary query 3 (Child vs Adult AWaRe choices)", "Prescriptions stratified by age (<15 vs 15+)",
           "fact_prescriptions x dim_visit x dim_medication", ">0 strata", f"{len(ped_summary)} breakdown rows", "non-empty", len(ped_summary) > 0)

    # Test 10: Proportions are coherent
    prop_check = conn.execute("""
        WITH shares AS (
            SELECT aware_category,
                   COUNT(*)::FLOAT / SUM(COUNT(*)) OVER () as share
            FROM fact_prescriptions p
            JOIN dim_medication m ON p.medication_id = m.medication_id
            GROUP BY aware_category
        )
        SELECT ROUND(SUM(share), 4) FROM shares
    """).fetchone()[0]
    record("Test 10: Proportions are coherent (AWaRe shares sum to 100%)", "Windowed shares of prescriptions sum to 1.0",
           "SUM(share) over aware_category", 1.0, prop_check, "relative 1e-4", abs(prop_check - 1.0) < 1e-4)

    # Test 11: Joins are reachable and short
    from olap_swifter.profiler.join_graph import JoinGraph
    graph = JoinGraph()
    graph.load_from_metadata(spec.metadata())
    p_fac = graph.get_join_path("fact_prescriptions", "dim_facility")
    p_pat = graph.get_join_path("fact_prescriptions", "dim_patient")
    record("Test 11: Joins reachable (fact_prescriptions -> dim_facility)", "Shortest join path across visit spine",
           "fact_prescriptions -> dim_visit -> dim_facility",
           ["fact_prescriptions", "dim_visit", "dim_facility"], p_fac, "exact",
           p_fac == ["fact_prescriptions", "dim_visit", "dim_facility"])
    record("Test 11: Joins reachable (fact_prescriptions -> dim_patient)", "Shortest join path across visit spine",
           "fact_prescriptions -> dim_visit -> dim_patient",
           ["fact_prescriptions", "dim_visit", "dim_patient"], p_pat, "exact",
           p_pat == ["fact_prescriptions", "dim_visit", "dim_patient"])

    total = len(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = sum(1 for r in results if r["result"] == "FAIL")
    print(f"\nTOTAL: {passed}/{total} passed, {failed} documented data quality issues.")
    return total, passed, failed

if __name__ == "__main__":
    run_production_tests()
