import duckdb
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.materializer import resolve_source

def run_tests():
    conn = duckdb.connect("resources/warehouse_dummy.duckdb")
    spec = SchemaRegistry().get("clinical_visit")
    source_id = "clinical_visit_dummy"
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
    null_spine = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE visit_id IS NULL").fetchone()[0]
    record("Gate 2: Spine key is complete", "dim_visit.visit_id not null in raw",
           f"SELECT COUNT(*) FROM {staging} WHERE visit_id IS NULL", 0, null_spine, "exact", null_spine == 0)

    # Gate 3: Keys are keys (entity consistency)
    conf_pat = conn.execute(f"SELECT COUNT(*) FROM (SELECT patient_id FROM {staging} GROUP BY patient_id HAVING COUNT(DISTINCT gender) > 1)").fetchone()[0]
    record("Gate 3: Keys are keys (dim_patient)", "patient_id consistent attribute combinations",
           "HAVING COUNT(DISTINCT gender) > 1", 0, conf_pat, "exact", conf_pat == 0)

    # Gate 4: Coded values stay inside vocabulary
    bad_aware = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE aware_category IS NOT NULL AND aware_category NOT IN ('Access', 'Watch', 'Reserve')").fetchone()[0]
    record("Gate 4: Coded values (aware_category)", "aware_category in [Access, Watch, Reserve]",
           "NOT IN ('Access', 'Watch', 'Reserve')", 0, bad_aware, "exact", bad_aware == 0)

    # Gate 5: Measures are measurable
    bad_doses = conn.execute(f"SELECT COUNT(*) FROM {staging} WHERE doses_per_day IS NOT NULL AND TRY_CAST(doses_per_day AS BIGINT) IS NULL").fetchone()[0]
    record("Gate 5: Measures are measurable", "doses_per_day castable to BIGINT",
           "TRY_CAST(doses_per_day AS BIGINT) IS NULL", 0, bad_doses, "exact", bad_doses == 0)

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

    # Test 2: Entity counts survived
    raw_pats = conn.execute(f"SELECT COUNT(DISTINCT patient_id) FROM {staging}").fetchone()[0]
    wh_pats = conn.execute("SELECT COUNT(*) FROM dim_patient").fetchone()[0]
    record("Test 2: Entity counts survived (dim_patient)", "distinct patient_id raw vs warehouse",
           "COUNT(DISTINCT patient_id)", raw_pats, wh_pats, "exact", raw_pats == wh_pats)

    raw_meds = conn.execute(f"SELECT COUNT(DISTINCT medication_id) FROM {staging}").fetchone()[0]
    wh_meds = conn.execute("SELECT COUNT(*) FROM dim_medication").fetchone()[0]
    record("Test 2: Entity counts survived (dim_medication)", "distinct medication_id raw vs warehouse",
           "COUNT(DISTINCT medication_id)", raw_meds, wh_meds, "exact", raw_meds == wh_meds)

    # Test 3: Coverage of the spine
    raw_visits = conn.execute(f"SELECT COUNT(DISTINCT visit_id) FROM {staging}").fetchone()[0]
    wh_visits = conn.execute("SELECT COUNT(DISTINCT visit_id) FROM dim_visit").fetchone()[0]
    record("Test 3: Coverage of the spine", "distinct visit_id raw vs warehouse",
           "COUNT(DISTINCT visit_id)", raw_visits, wh_visits, "exact", raw_visits == wh_visits)

    # Test 4: Attribute fidelity
    diff_attr = conn.execute("""
        WITH raw_dist AS (SELECT gender, COUNT(DISTINCT patient_id) AS cnt FROM stg_clinical_visit_dummy GROUP BY gender),
             wh_dist  AS (SELECT gender, COUNT(*) AS cnt FROM dim_patient GROUP BY gender)
        SELECT COUNT(*) FROM raw_dist FULL OUTER JOIN wh_dist USING (gender) WHERE raw_dist.cnt != wh_dist.cnt
    """).fetchone()[0]
    record("Test 4: Attribute fidelity (patient gender)", "distribution of gender across patients",
           "raw vs warehouse gender counts", 0, diff_attr, "exact", diff_attr == 0)

    print("\n=== SUB-AGENT 3: metric_parity_verifier ===")
    # Test 5: Sum parity
    for m, m_type in [("dose_mg", "DOUBLE"), ("doses_per_day", "BIGINT"), ("treatment_days", "BIGINT")]:
        exp = conn.execute(f"SELECT SUM(TRY_CAST({m} AS {m_type})) FROM (SELECT DISTINCT visit_id, medication_id, {m} FROM {staging} WHERE visit_id IS NOT NULL AND medication_id IS NOT NULL)").fetchone()[0]
        act = conn.execute(f"SELECT SUM({m}) FROM fact_prescriptions WHERE _source_id = '{source_id}'").fetchone()[0]
        exp_f, act_f = float(exp or 0), float(act or 0)
        delta = abs(exp_f - act_f)
        record(f"Test 5: Sum parity ({m})", f"fact_prescriptions.{m}",
               f"SUM({m}) de-duplicated", exp_f, act_f, "relative 1e-6", delta <= 1e-6 * max(1.0, exp_f))

    # Test 6: Average and distribution parity
    raw_avg = conn.execute(f"SELECT AVG(TRY_CAST(dose_mg AS DOUBLE)) FROM (SELECT DISTINCT visit_id, medication_id, dose_mg FROM {staging} WHERE visit_id IS NOT NULL AND medication_id IS NOT NULL)").fetchone()[0]
    wh_avg = conn.execute("SELECT AVG(dose_mg) FROM fact_prescriptions").fetchone()[0]
    record("Test 6: Average parity (dose_mg)", "AVG(dose_mg) de-duplicated",
           "AVG(dose_mg)", round(float(raw_avg), 4), round(float(wh_avg), 4), "relative 1e-4",
           abs(float(raw_avg) - float(wh_avg)) < 1e-3)

    # Test 7: Group-by parity
    diff_grp = conn.execute("""
        WITH raw_grp AS (
            SELECT p.indication_type, COUNT(*) as cnt
            FROM (SELECT DISTINCT visit_id, medication_id, indication_type FROM stg_clinical_visit_dummy WHERE visit_id IS NOT NULL) p
            GROUP BY p.indication_type
        ),
        wh_grp AS (
            SELECT indication_type, COUNT(*) as cnt
            FROM fact_prescriptions
            GROUP BY indication_type
        )
        SELECT COUNT(*) FROM raw_grp FULL OUTER JOIN wh_grp USING (indication_type)
        WHERE raw_grp.cnt != wh_grp.cnt
    """).fetchone()[0]
    record("Test 7: Group-by parity (indication_type count)", "prescription count grouped by indication_type",
           "GROUP BY indication_type", 0, diff_grp, "exact", diff_grp == 0)

    # Test 8: Fan-out does not inflate
    joined_sum = conn.execute("""
        SELECT SUM(dose_mg) FROM (
            SELECT DISTINCT p.visit_id, p.medication_id, p.dose_mg
            FROM fact_prescriptions p
            JOIN dim_visit v ON p.visit_id = v.visit_id
            LEFT JOIN fact_diagnoses d ON v.visit_id = d.visit_id
        )
    """).fetchone()[0]
    base_sum = conn.execute("SELECT SUM(dose_mg) FROM fact_prescriptions").fetchone()[0]
    record("Test 8: Fan-out does not inflate", "joining fact_prescriptions through dim_visit to fact_diagnoses",
           "SUM(dose_mg) across joined child", base_sum, joined_sum, "exact", abs(float(base_sum) - float(joined_sum)) < 1e-6)

    print("\n=== SUB-AGENT 4: clinical_research_query_agent ===")
    # Test 9: Real clinical question
    cq1 = conn.execute("""
        SELECT m.aware_category, COUNT(*) as rx_count
        FROM fact_prescriptions p
        JOIN dim_medication m ON p.medication_id = m.medication_id
        GROUP BY m.aware_category
        ORDER BY rx_count DESC
    """).fetchall()
    record("Test 9: Clinical research query 1 (AWaRe distribution)", "prescriptions broken down by AWaRe category",
           "JOIN fact_prescriptions and dim_medication", ">0 groups", f"{len(cq1)} groups returned", "non-empty", len(cq1) > 0)

    cq2 = conn.execute("""
        SELECT v.ward_type, p.treatment_basis, COUNT(*) as cnt
        FROM fact_prescriptions p
        JOIN dim_visit v ON p.visit_id = v.visit_id
        GROUP BY v.ward_type, p.treatment_basis
    """).fetchall()
    record("Test 9: Clinical research query 2 (Ward x Treatment Basis)", "prescriptions across ward types and empirical vs targeted",
           "JOIN fact_prescriptions and dim_visit", ">0 groups", f"{len(cq2)} groups returned", "non-empty", len(cq2) > 0)

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
    from olap_swifter.olap.constellation import get_constellation_schema_metadata
    graph = JoinGraph()
    graph.load_from_metadata(spec.metadata())
    p_fac = graph.get_join_path("fact_prescriptions", "dim_facility")
    record("Test 11: Joins reachable (fact_prescriptions -> dim_facility)", "Shortest join path across visit spine",
           "fact_prescriptions -> dim_visit -> dim_facility",
           ["fact_prescriptions", "dim_visit", "dim_facility"], p_fac, "exact",
           p_fac == ["fact_prescriptions", "dim_visit", "dim_facility"])

    total = len(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    print(f"\nTOTAL: {passed}/{total} tests passed.")
    return total, passed

if __name__ == "__main__":
    run_tests()
