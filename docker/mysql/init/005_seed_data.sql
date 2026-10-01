-- 全部记录为合成测试数据，覆盖四等级及筛选、时间和映射边界。
-- All records are synthetic, covering four levels and selection, time and mapping boundaries.

USE mini_mpc_insurer_test;

INSERT INTO insurer_inpatient_records (
    insured_person_id,
    id_card_no,
    inpatient_record_id,
    hospital_id,
    admission_time,
    is_currently_inpatient,
    inpatient_status
) VALUES (
    'insured-001',
    '110101199001010011',
    'insurer-inpatient-001',
    'hospital-001',
    '2026-08-28 21:30:00',
    1,
    'emergency'
),
(
    'insured-002',
    '110101199001010022',
    'insurer-inpatient-002',
    'hospital-001',
    '2026-08-28 18:10:00',
    1,
    'routine'
),
(
    'insured-003',
    '110101199001010033',
    'insurer-inpatient-003',
    'hospital-001',
    '2026-08-28 17:40:00',
    1,
    'routine'
),
(
    'insured-004',
    '110101199001010044',
    'insurer-inpatient-004',
    'hospital-002',
    '2026-08-28 20:00:00',
    1,
    'emergency'
),
(
    'insured-007',
    '110101199001010077',
    'insurer-inpatient-007',
    'hospital-001',
    '2026-08-28 19:00:00',
    1,
    'routine'
),
(
    'insured-005',
    '110101199001010055',
    'insurer-inpatient-005',
    'hospital-001',
    '2026-08-28 21:00:00',
    0,
    'routine'
),
(
    'insured-006',
    '110101199001010066',
    'insurer-inpatient-006',
    'hospital-001',
    '2026-08-28 22:30:00',
    1,
    'routine'
);

USE mini_mpc_telco_test;

INSERT INTO telco_hospital_cell_map (
    hospital_id,
    nCGI,
    TAI,
    mapping_version,
    valid_from,
    valid_to
) VALUES (
    'hospital-001',
    'ncgi-hospital-001',
    'tai-001',
    'v1',
    '2026-01-01 00:00:00',
    NULL
),
(
    'hospital-002',
    'ncgi-hospital-002',
    'tai-002',
    'v1',
    '2026-01-01 00:00:00',
    NULL
),
(
    'hospital-001',
    'ncgi-expired-001',
    'tai-001',
    'old',
    '2025-01-01 00:00:00',
    '2026-01-01 00:00:00'
);

INSERT INTO telco_audit_requests (
    patient_link_key,
    subscriber_id,
    hospital_id,
    audit_date
) VALUES
    ('patient-link-001', 'subscriber-001', 'hospital-001', '2026-08-28'),
    ('patient-link-002', 'subscriber-002', 'hospital-001', '2026-08-28'),
    ('patient-link-003', 'subscriber-003', 'hospital-001', '2026-08-28'),
    ('patient-link-004', 'subscriber-004', 'hospital-002', '2026-08-28'),
    ('patient-link-007', 'subscriber-007', 'hospital-001', '2026-08-28');

INSERT INTO telco_location_events (
    subscriber_id,
    patient_link_key,
    location_timestamp,
    nCGI,
    TAI
) VALUES
    ('subscriber-001', 'patient-link-001', '2026-08-28 22:15:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-001', 'patient-link-001', '2026-08-28 23:05:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-001', 'patient-link-001', '2026-08-29 03:10:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-001', 'patient-link-001', '2026-08-29 04:25:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-001', 'patient-link-001', '2026-08-29 07:40:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-28 22:05:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-28 23:10:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 00:20:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 01:30:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 02:15:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 03:50:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 04:10:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 05:05:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 06:45:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-002', 'patient-link-002', '2026-08-29 07:55:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-28 22:05:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-28 23:10:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 00:20:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 01:30:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 02:15:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 03:50:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 04:10:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-007', 'patient-link-007', '2026-08-29 05:05:00', 'ncgi-hospital-001', 'tai-001'),
    ('subscriber-004', 'patient-link-004', '2026-08-28 22:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-28 23:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 00:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 01:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 02:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 03:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 04:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 05:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 06:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-004', 'patient-link-004', '2026-08-29 07:35:00', 'ncgi-hospital-002', 'tai-002'),
    ('subscriber-001', 'patient-link-001', '2026-08-29 02:15:00', 'ncgi-hospital-001', 'wrong-tai'),
    ('subscriber-001', 'patient-link-001', '2026-08-29 05:15:00', 'ncgi-expired-001', 'tai-001');

USE mini_mpc_hospital_test;

INSERT INTO hospital_inpatient_records (
    patient_link_key,
    hospital_inpatient_record_id,
    hospital_id,
    diagnosis_code,
    disease_category,
    severity_level
) VALUES (
    'patient-link-001',
    'hospital-inpatient-001',
    'hospital-001',
    'J18.9',
    'respiratory',
    'moderate'
),
(
    'patient-link-002',
    'hospital-inpatient-002',
    'hospital-001',
    'S52.5',
    'orthopedic',
    'mild'
),
(
    'patient-link-003',
    'hospital-inpatient-003',
    'hospital-001',
    'Z00.0',
    'administrative',
    'mild'
),
(
    'patient-link-004',
    'hospital-inpatient-004',
    'hospital-002',
    'I21.9',
    'cardiac',
    'severe'
),
(
    'patient-link-007',
    'hospital-inpatient-007',
    'hospital-001',
    'M54.5',
    'orthopedic',
    'mild'
);

INSERT INTO hospital_clinical_score_rules (
    disease_category,
    severity_level,
    clinical_need_score
) VALUES (
    'respiratory',
    'moderate',
    6
),
(
    'orthopedic',
    'mild',
    2
),
(
    'administrative',
    'mild',
    10
),
(
    'cardiac',
    'severe',
    0
);
