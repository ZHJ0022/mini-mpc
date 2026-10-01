-- 医院本地规则表生成临床风险分；reader 只读取评分 view。
-- Local hospital rules derive risk scores; the reader accesses only the scoring view.

USE mini_mpc_hospital_test;

CREATE TABLE hospital_inpatient_records (
    patient_link_key VARCHAR(64) NOT NULL,
    hospital_inpatient_record_id VARCHAR(64) NOT NULL PRIMARY KEY,
    hospital_id VARCHAR(64) NOT NULL,
    diagnosis_code VARCHAR(32) NOT NULL,
    disease_category VARCHAR(64) NOT NULL,
    severity_level VARCHAR(32) NOT NULL,
    INDEX idx_hospital_patient (patient_link_key, hospital_id)
);

CREATE TABLE hospital_clinical_score_rules (
    disease_category VARCHAR(64) NOT NULL,
    severity_level VARCHAR(32) NOT NULL,
    clinical_need_score INT NOT NULL,
    PRIMARY KEY (disease_category, severity_level),
    CHECK (clinical_need_score BETWEEN 0 AND 10)
);

CREATE VIEW v_hospital_clinical_scoring AS
SELECT
    records.patient_link_key,
    records.hospital_inpatient_record_id,
    records.hospital_id,
    records.diagnosis_code,
    records.disease_category,
    records.severity_level,
    rules.clinical_need_score
FROM hospital_inpatient_records AS records
JOIN hospital_clinical_score_rules AS rules
  ON records.disease_category = rules.disease_category
 AND records.severity_level = rules.severity_level;

GRANT SELECT ON mini_mpc_hospital_test.v_hospital_clinical_scoring
TO 'mini_mpc_hospital_reader'@'%';
