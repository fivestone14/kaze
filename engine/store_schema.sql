-- Kaze store schema: the 15 tables of the four-pillar SPX store (registry, collect, train, reads, results).
-- Rendered from the store design (STORE.json, 2026-10-16); Parquet partition keys are plain columns here, indexed below.

-- 0 REGISTRY - what every number and every versioned thing means
-- registry_measures: Defines every measure any long table holds, plus features and labels
-- grain: one row per measure x measure version
CREATE TABLE IF NOT EXISTS registry_measures (
    measure_name TEXT,
    measure_version INTEGER,
    plain_meaning_text TEXT,
    pillar_name TEXT,
    stored_in_table_name TEXT,
    subject_kinds_text TEXT,
    subject_key_format TEXT,
    value_type TEXT,
    value_column TEXT,
    unit TEXT,
    allowed_words_text TEXT,
    allowed_minimum_number REAL,
    allowed_maximum_number REAL,
    signal_lane TEXT,
    information_group_name TEXT,
    overlap_group_name TEXT,
    positive_value_means TEXT,
    neutral_value_number REAL,
    is_history_derived INTEGER,
    history_divisor_measure_name TEXT,
    raw_source_fields_text TEXT,
    source_feed_name TEXT,
    recipe_component_name TEXT,
    blank_means_text TEXT,
    max_carry_forward_seconds REAL,
    coverage_twin_measure_name TEXT,
    wide_column_template TEXT,
    available_since_trade_date TEXT,
    available_from_minutes_after_open REAL,
    history_complete_since_date TEXT,
    is_cut_input INTEGER,
    cut_reason_text TEXT,
    is_station_only INTEGER,
    legacy_columns_text TEXT,
    defined_at TEXT,
    retired_at TEXT,
    PRIMARY KEY (measure_name, measure_version)
);

-- registry_components: Every versioned feed, recipe, rule, forecaster, rulebook, feature set, model
-- grain: one row per kind x name x version x revision (an approval or evidence change is a new revision)
CREATE TABLE IF NOT EXISTS registry_components (
    component_kind TEXT,
    component_name TEXT,
    component_version TEXT,
    revision_number INTEGER,
    version_sequence_number INTEGER,
    previous_component_version TEXT,
    parent_components_text TEXT,
    plain_meaning_text TEXT,
    signal_lane TEXT,
    overlap_group_name TEXT,
    input_measures_text TEXT,
    output_measures_text TEXT,
    parameters_json_text TEXT,
    parameters_sha256 TEXT,
    code_commit TEXT,
    source_sha256 TEXT,
    environment_sha256 TEXT,
    golden_output_sha256 TEXT,
    does_change_output_values INTEGER,
    does_change_meaning INTEGER,
    tuned_from_trade_date TEXT,
    tuned_through_trade_date TEXT,
    starting_trust_weight_portion REAL,
    evidence_status TEXT,
    proven_on_date TEXT,
    trust_gate_rule_version TEXT,
    approval_status TEXT,
    storage_scope TEXT,
    artifact_s3_key TEXT,
    artifact_sha256 TEXT,
    released_at TEXT,
    retired_at TEXT,
    written_at TEXT,
    PRIMARY KEY (component_kind, component_name, component_version, revision_number)
);

-- 1 COLLECT - everything the model can see, as known at each cut
-- collect_snapshots: The spine: every frozen cut with build, origin and health summary
-- grain: one row per cut x row origin x builder version x build attempt
CREATE TABLE IF NOT EXISTS collect_snapshots (
    snapshot_id TEXT,
    trade_date TEXT,
    snapshot_kind TEXT,
    read_id TEXT,
    read_lane TEXT,
    read_lane_cadence_minutes REAL,
    scheduled_slot_eastern TEXT,
    slot_status TEXT,
    slot_lateness_minutes REAL,
    market_moment_id TEXT,
    is_training_grid_read INTEGER,
    read_moment_set_by TEXT,
    cut_at TEXT,
    known_at TEXT,
    known_at_rule TEXT,
    row_origin TEXT,
    build_attempt_number INTEGER,
    builder_name TEXT,
    builder_version TEXT,
    builder_code_commit TEXT,
    was_code_tree_dirty INTEGER,
    component_versions_json_text TEXT,
    build_started_at TEXT,
    build_seconds REAL,
    did_exceed_build_budget INTEGER,
    source_manifest_s3_key TEXT,
    source_manifest_sha256 TEXT,
    snapshot_content_sha256 TEXT,
    feeds_not_ok_count INTEGER,
    blank_values_count INTEGER,
    blank_values_without_reason_count INTEGER,
    snapshot_status TEXT,
    snapshot_status_reason TEXT,
    written_at TEXT,
    PRIMARY KEY (snapshot_id)
);
CREATE INDEX IF NOT EXISTS ix_collect_snapshots_trade_date_cut_at ON collect_snapshots (trade_date, cut_at);
CREATE INDEX IF NOT EXISTS ix_collect_snapshots_row_origin_cut_at ON collect_snapshots (row_origin, cut_at);
CREATE INDEX IF NOT EXISTS ix_collect_snapshots_snapshot_kind_trade_date ON collect_snapshots (snapshot_kind, trade_date);

-- collect_observations: Every reading the model may see, one value per row
-- grain: one row per snapshot x measure x measure version x subject x value origin
CREATE TABLE IF NOT EXISTS collect_observations (
    observation_id TEXT,
    snapshot_id TEXT,
    trade_date TEXT,
    cut_at TEXT,
    known_at TEXT,
    row_origin TEXT,
    measure_name TEXT,
    measure_version INTEGER,
    subject_key TEXT,
    subject_points REAL,
    value_number REAL,
    value_bool INTEGER,
    value_text TEXT,
    value_at TEXT,
    value_origin TEXT,
    blank_reason TEXT,
    source_feed_name TEXT,
    feed_status TEXT,
    feed_age_seconds REAL,
    newest_input_market_at TEXT,
    newest_input_received_at TEXT,
    stand_in_source_name TEXT,
    PRIMARY KEY (observation_id)
);
CREATE INDEX IF NOT EXISTS ix_collect_observations_snapshot_id ON collect_observations (snapshot_id);
CREATE INDEX IF NOT EXISTS ix_collect_observations_measure_name_trade_date ON collect_observations (measure_name, trade_date);

-- collect_market_prices: Bars, quotes, option chain, open interest, greeks and settlements
-- grain: one row per instrument x price kind x priced_at x snapshot (blank for streaming bars) x value origin x row origin
CREATE TABLE IF NOT EXISTS collect_market_prices (
    price_record_id TEXT,
    trade_date TEXT,
    instrument_symbol TEXT,
    instrument_kind TEXT,
    price_kind TEXT,
    bar_minutes REAL,
    priced_at TEXT,
    known_at TEXT,
    known_at_rule TEXT,
    snapshot_id TEXT,
    row_origin TEXT,
    value_origin TEXT,
    source_feed_name TEXT,
    feed_status TEXT,
    quote_unit TEXT,
    open_in_quote_units REAL,
    high_in_quote_units REAL,
    low_in_quote_units REAL,
    close_in_quote_units REAL,
    last_in_quote_units REAL,
    prior_close_in_quote_units REAL,
    is_prior_close_a_stand_in INTEGER,
    bid_in_quote_units REAL,
    ask_in_quote_units REAL,
    broker_mark_in_quote_units REAL,
    bid_size_count INTEGER,
    ask_size_count INTEGER,
    volume_count INTEGER,
    volume_bought_at_ask_count INTEGER,
    volume_sold_at_bid_count INTEGER,
    volume_unsided_count INTEGER,
    open_interest_count INTEGER,
    open_interest_published_for_date TEXT,
    option_root TEXT,
    expiration_date TEXT,
    strike_points REAL,
    call_or_put TEXT,
    settlement_style TEXT,
    implied_vol_calendar_year_pct REAL,
    implied_vol_trading_year_pct REAL,
    delta_per_point REAL,
    gamma_per_point REAL,
    theta_per_share_per_day_usd REAL,
    vega_per_share_per_vol_point_usd REAL,
    greeks_source TEXT,
    quote_status TEXT,
    blank_reasons_text TEXT,
    PRIMARY KEY (price_record_id)
);
CREATE INDEX IF NOT EXISTS ix_collect_market_prices_instrument_symbol_priced_at ON collect_market_prices (instrument_symbol, priced_at);
CREATE INDEX IF NOT EXISTS ix_collect_market_prices_trade_date_row_origin ON collect_market_prices (trade_date, row_origin);

-- collect_calendar_and_events: Sessions, scheduled events, halts, outages, source defects and headlines
-- grain: one row per fact x fact version
CREATE TABLE IF NOT EXISTS collect_calendar_and_events (
    fact_record_id TEXT,
    fact_kind TEXT,
    fact_id TEXT,
    fact_version TEXT,
    trade_date TEXT,
    last_trade_date TEXT,
    starts_at TEXT,
    ends_at TEXT,
    outage_end_basis TEXT,
    known_at TEXT,
    publicly_scheduled_since_date TEXT,
    fact_name TEXT,
    fact_category TEXT,
    calendar_tier TEXT,
    event_impact TEXT,
    is_information_release INTEGER,
    has_audience_questions INTEGER,
    was_scheduled_in_advance INTEGER,
    fact_status TEXT,
    rows_roll_to_trade_date TEXT,
    session_opens_at TEXT,
    session_closes_at TEXT,
    expiring_spxw_last_trade_at TEXT,
    is_bond_market_closed INTEGER,
    bond_market_closes_at TEXT,
    is_monthly_expiry INTEGER,
    is_quarterly_expiry INTEGER,
    has_index_rebalance_at_close INTEGER,
    trading_session_number INTEGER,
    related_feed_name TEXT,
    related_measures_text TEXT,
    was_stand_in_used INTEGER,
    defect_handling TEXT,
    source_reference_text TEXT,
    detail_scrubbed_text TEXT,
    row_origin TEXT,
    written_at TEXT,
    PRIMARY KEY (fact_record_id)
);
CREATE INDEX IF NOT EXISTS ix_collect_calendar_and_events_trade_date ON collect_calendar_and_events (trade_date);
CREATE INDEX IF NOT EXISTS ix_collect_calendar_and_events_fact_kind_trade_date ON collect_calendar_and_events (fact_kind, trade_date);

-- 2 TRAIN - point-in-time, model-ready rows, splits and recipes
-- train_feature_set_columns: Frozen ordered recipe of each wide training row and Claude prompt
-- grain: one row per feature set version x column position
CREATE TABLE IF NOT EXISTS train_feature_set_columns (
    feature_set_name TEXT,
    feature_set_version TEXT,
    column_position_number INTEGER,
    export_column_name TEXT,
    column_role TEXT,
    consumer_kind TEXT,
    source_table_name TEXT,
    measure_name TEXT,
    measure_version INTEGER,
    subject_selector TEXT,
    source_decision_kind TEXT,
    source_producer_name TEXT,
    source_value_role TEXT,
    source_value_origin TEXT,
    source_build_rule TEXT,
    source_run_kinds_text TEXT,
    source_variant_name TEXT,
    horizon_rule TEXT,
    pick_rule TEXT,
    max_value_age_seconds REAL,
    transform_name TEXT,
    divided_by_column_name TEXT,
    transform_rule_version TEXT,
    missing_value_handling TEXT,
    blank_when_status_in_text TEXT,
    signal_lane TEXT,
    information_group_name TEXT,
    category_vocabulary_text TEXT,
    earliest_complete_trade_date TEXT,
    feature_set_sha256 TEXT,
    frozen_at TEXT,
    PRIMARY KEY (feature_set_name, feature_set_version, column_position_number)
);

-- train_examples: Frozen point-in-time training rows: features as known, labels as resolved
-- grain: one row per example kind x scored unit x feature set version x label set version x materialization
CREATE TABLE IF NOT EXISTS train_examples (
    example_id TEXT,
    example_kind TEXT,
    feature_set_name TEXT,
    feature_set_version TEXT,
    label_set_name TEXT,
    label_set_version TEXT,
    trade_date TEXT,
    read_id TEXT,
    snapshot_id TEXT,
    scored_decision_id TEXT,
    outcome_id TEXT,
    horizon_name TEXT,
    graded_price_measure TEXT,
    subject_key TEXT,
    ranking_group_id TEXT,
    inference_moment_rule TEXT,
    features_cutoff_at TEXT,
    latest_feature_known_at TEXT,
    label_resolved_at TEXT,
    row_origin TEXT,
    example_weight REAL,
    feature_values TEXT,
    feature_text_values TEXT,
    feature_blank_reasons TEXT,
    label_values TEXT,
    label_blank_reasons TEXT,
    label_status TEXT,
    missing_features_count INTEGER,
    is_excluded INTEGER,
    exclusion_reason TEXT,
    materialized_at TEXT,
    materialization_number INTEGER,
    builder_code_commit TEXT,
    PRIMARY KEY (example_id)
);
CREATE INDEX IF NOT EXISTS ix_train_examples_trade_date ON train_examples (trade_date);
CREATE INDEX IF NOT EXISTS ix_train_examples_snapshot_id ON train_examples (snapshot_id);

-- train_splits: Walk-forward role of each session per fold, with locked hold-out
-- grain: one row per split plan x version x fold x session
CREATE TABLE IF NOT EXISTS train_splits (
    split_plan_name TEXT,
    split_plan_version TEXT,
    fold_number INTEGER,
    trade_date TEXT,
    day_role TEXT,
    exclusion_reason TEXT,
    is_exclusion_known_after_day INTEGER,
    fold_first_test_trade_date TEXT,
    fold_last_train_trade_date TEXT,
    times_scored_before_count INTEGER,
    graded_examples_count INTEGER,
    session_slot_coverage_portion REAL,
    created_at TEXT,
    PRIMARY KEY (split_plan_name, split_plan_version, fold_number, trade_date)
);

-- 3 READS - every decision the process made
-- reads_decisions: One header per decision: forecasts, Claude, calls, every trade candidate
-- grain: one row per decision revision
CREATE TABLE IF NOT EXISTS reads_decisions (
    decision_id TEXT,
    trade_date TEXT,
    storage_scope TEXT,
    decision_kind TEXT,
    parent_decision_id TEXT,
    input_decision_ids_text TEXT,
    snapshot_id TEXT,
    read_id TEXT,
    cut_at TEXT,
    features_cutoff_at TEXT,
    known_at TEXT,
    row_origin TEXT,
    run_kind TEXT,
    variant_name TEXT,
    producer_kind TEXT,
    producer_name TEXT,
    producer_version TEXT,
    builder_code_commit TEXT,
    horizon_name TEXT,
    horizon_minutes REAL,
    graded_price_measure TEXT,
    grade_start_rule_version TEXT,
    grade_window_starts_at TEXT,
    grade_window_ends_at TEXT,
    subject_key TEXT,
    trade_structure_name TEXT,
    trade_mode TEXT,
    revision_number INTEGER,
    decision_status TEXT,
    decision_status_reason TEXT,
    decision_word TEXT,
    decision_text TEXT,
    input_text_s3_key TEXT,
    output_text_s3_key TEXT,
    input_sha256 TEXT,
    written_at TEXT,
    PRIMARY KEY (decision_id)
);
CREATE INDEX IF NOT EXISTS ix_reads_decisions_trade_date ON reads_decisions (trade_date);
CREATE INDEX IF NOT EXISTS ix_reads_decisions_snapshot_id ON reads_decisions (snapshot_id);
CREATE INDEX IF NOT EXISTS ix_reads_decisions_read_id ON reads_decisions (read_id);

-- reads_decision_values: Every number, word, gate, text line and moment inside a decision
-- grain: one row per decision x measure x version x subject x value role
CREATE TABLE IF NOT EXISTS reads_decision_values (
    decision_value_id TEXT,
    decision_id TEXT,
    trade_date TEXT,
    storage_scope TEXT,
    decision_kind TEXT,
    known_at TEXT,
    row_origin TEXT,
    measure_name TEXT,
    measure_version INTEGER,
    subject_key TEXT,
    subject_points REAL,
    value_role TEXT,
    value_number REAL,
    value_bool INTEGER,
    value_text TEXT,
    value_at TEXT,
    blank_reason TEXT,
    trust_weight_applied_portion REAL,
    trust_evaluation_row_id TEXT,
    PRIMARY KEY (decision_value_id)
);
CREATE INDEX IF NOT EXISTS ix_reads_decision_values_decision_id ON reads_decision_values (decision_id);
CREATE INDEX IF NOT EXISTS ix_reads_decision_values_trade_date ON reads_decision_values (trade_date);

-- reads_trade_legs: Legs of every trade-shaped decision, any structure, priced quote
-- grain: one row per decision x leg
CREATE TABLE IF NOT EXISTS reads_trade_legs (
    leg_record_id TEXT,
    decision_id TEXT,
    trade_date TEXT,
    storage_scope TEXT,
    known_at TEXT,
    row_origin TEXT,
    leg_number INTEGER,
    leg_role TEXT,
    option_contract_symbol TEXT,
    option_root TEXT,
    expiration_date TEXT,
    strike_points REAL,
    call_or_put TEXT,
    signed_quantity_per_spread_count INTEGER,
    quote_price_record_id TEXT,
    PRIMARY KEY (leg_record_id)
);
CREATE INDEX IF NOT EXISTS ix_reads_trade_legs_decision_id ON reads_trade_legs (decision_id);

-- 4 RESULTS - what actually happened and how each decision graded
-- results_outcomes: One header per outcome, grade, would-have-earned result or fill
-- grain: one row per outcome kind x anchor x judged decision x horizon x price measure x grade-start rule x outcome rule x exit rule x row origin x build attempt
CREATE TABLE IF NOT EXISTS results_outcomes (
    outcome_id TEXT,
    trade_date TEXT,
    storage_scope TEXT,
    outcome_kind TEXT,
    anchor_id TEXT,
    judged_decision_id TEXT,
    market_outcome_id TEXT,
    read_id TEXT,
    horizon_name TEXT,
    graded_price_measure TEXT,
    grade_start_rule_version TEXT,
    window_starts_at TEXT,
    window_ends_at TEXT,
    outcome_rule_name TEXT,
    outcome_rule_version TEXT,
    exit_rule_name TEXT,
    exit_rule_version TEXT,
    fee_schedule_version TEXT,
    trade_mode TEXT,
    label_status TEXT,
    label_status_reason TEXT,
    label_resolved_at TEXT,
    does_count_toward_skill INTEGER,
    row_origin TEXT,
    build_attempt_number INTEGER,
    builder_code_commit TEXT,
    written_at TEXT,
    PRIMARY KEY (outcome_id)
);
CREATE INDEX IF NOT EXISTS ix_results_outcomes_trade_date ON results_outcomes (trade_date);
CREATE INDEX IF NOT EXISTS ix_results_outcomes_judged_decision_id ON results_outcomes (judged_decision_id);
CREATE INDEX IF NOT EXISTS ix_results_outcomes_anchor_id ON results_outcomes (anchor_id);

-- results_outcome_values: Every fact of an outcome: prices, touches, grades, profits
-- grain: one row per outcome x measure x version x subject
CREATE TABLE IF NOT EXISTS results_outcome_values (
    outcome_value_id TEXT,
    outcome_id TEXT,
    trade_date TEXT,
    storage_scope TEXT,
    outcome_kind TEXT,
    measure_name TEXT,
    measure_version INTEGER,
    subject_key TEXT,
    subject_points REAL,
    value_number REAL,
    value_bool INTEGER,
    value_text TEXT,
    value_at TEXT,
    value_status TEXT,
    blank_reason TEXT,
    label_resolved_at TEXT,
    row_origin TEXT,
    PRIMARY KEY (outcome_value_id)
);
CREATE INDEX IF NOT EXISTS ix_results_outcome_values_outcome_id ON results_outcome_values (outcome_id);
CREATE INDEX IF NOT EXISTS ix_results_outcome_values_trade_date ON results_outcome_values (trade_date);

-- results_evaluations: Judgements over many results: trust weights, skill, calibration, tests
-- grain: one row per evaluation x evaluated component version x slice x metric
CREATE TABLE IF NOT EXISTS results_evaluations (
    evaluation_row_id TEXT,
    evaluation_id TEXT,
    evaluation_kind TEXT,
    trade_date TEXT,
    evaluated_kind TEXT,
    evaluated_name TEXT,
    evaluated_version TEXT,
    judge_name TEXT,
    judge_version TEXT,
    signal_lane TEXT,
    overlap_group_name TEXT,
    horizon_name TEXT,
    graded_price_measure TEXT,
    slice_key TEXT,
    metric_name TEXT,
    metric_value REAL,
    metric_text TEXT,
    blank_reason TEXT,
    metric_interval_low REAL,
    metric_interval_high REAL,
    interval_method TEXT,
    baseline_name TEXT,
    baseline_metric_value REAL,
    sessions_count INTEGER,
    independent_windows_count INTEGER,
    rows_count INTEGER,
    live_frozen_rows_count INTEGER,
    data_from_trade_date TEXT,
    data_through_trade_date TEXT,
    applies_from_at TEXT,
    was_pre_registered INTEGER,
    test_family_name TEXT,
    test_family_size_count INTEGER,
    detail_text TEXT,
    known_at TEXT,
    row_origin TEXT,
    builder_code_commit TEXT,
    PRIMARY KEY (evaluation_row_id)
);
CREATE INDEX IF NOT EXISTS ix_results_evaluations_trade_date ON results_evaluations (trade_date);
CREATE INDEX IF NOT EXISTS ix_results_evaluations_evaluated_name_evaluated_version ON results_evaluations (evaluated_name, evaluated_version);
