-- 医保 view 隐去身份证号；reader 只获该 view 的 SELECT 权限。
-- The insurer view excludes ID cards; the reader gets SELECT on this view only.

USE mini_mpc_insurer_test;

CREATE TABLE insurer_inpatient_records (
    insured_person_id VARCHAR(64) NOT NULL,
    id_card_no VARCHAR(32) NOT NULL,
    inpatient_record_id VARCHAR(64) NOT NULL PRIMARY KEY,
    hospital_id VARCHAR(64) NOT NULL,
    admission_time DATETIME NOT NULL,
    is_currently_inpatient TINYINT(1) NOT NULL,
    inpatient_status VARCHAR(32) NOT NULL
);

CREATE VIEW v_insurer_active_inpatients AS
SELECT
    insured_person_id,
    inpatient_record_id,
    hospital_id,
    admission_time,
    is_currently_inpatient,
    inpatient_status
FROM insurer_inpatient_records
WHERE is_currently_inpatient = 1;

GRANT SELECT ON mini_mpc_insurer_test.v_insurer_active_inpatients
TO 'mini_mpc_insurer_reader'@'%';
