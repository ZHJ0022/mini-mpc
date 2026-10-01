-- 按授权请求聚合十小时标记；无匹配事件的请求仍保留为全零。
-- Aggregate ten hourly flags per authorized request, retaining zero-event requests.

USE mini_mpc_telco_test;

CREATE TABLE telco_location_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    subscriber_id VARCHAR(64) NOT NULL,
    patient_link_key VARCHAR(64) NOT NULL,
    location_timestamp DATETIME NOT NULL,
    nCGI VARCHAR(64) NOT NULL,
    TAI VARCHAR(64) NULL,
    INDEX idx_telco_patient_time (patient_link_key, location_timestamp),
    INDEX idx_telco_cell (nCGI, TAI)
);

CREATE TABLE telco_hospital_cell_map (
    hospital_id VARCHAR(64) NOT NULL,
    nCGI VARCHAR(64) NOT NULL,
    TAI VARCHAR(64) NULL,
    mapping_version VARCHAR(32) NOT NULL,
    valid_from DATETIME NOT NULL,
    valid_to DATETIME NULL,
    PRIMARY KEY (hospital_id, nCGI, mapping_version)
);

CREATE TABLE telco_audit_requests (
    request_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    patient_link_key VARCHAR(64) NOT NULL,
    subscriber_id VARCHAR(64) NOT NULL,
    hospital_id VARCHAR(64) NOT NULL,
    audit_date DATE NOT NULL,
    UNIQUE KEY uq_telco_audit_request (patient_link_key, hospital_id, audit_date),
    INDEX idx_telco_audit_subscriber (subscriber_id, audit_date)
);

CREATE VIEW v_telco_hourly_presence AS
SELECT
    requests.subscriber_id,
    requests.patient_link_key,
    requests.hospital_id,
    requests.audit_date,
    COALESCE(MAX(CASE WHEN matched.hour_index = 0 THEN 1 ELSE 0 END), 0) AS presence_h00,
    COALESCE(MAX(CASE WHEN matched.hour_index = 1 THEN 1 ELSE 0 END), 0) AS presence_h01,
    COALESCE(MAX(CASE WHEN matched.hour_index = 2 THEN 1 ELSE 0 END), 0) AS presence_h02,
    COALESCE(MAX(CASE WHEN matched.hour_index = 3 THEN 1 ELSE 0 END), 0) AS presence_h03,
    COALESCE(MAX(CASE WHEN matched.hour_index = 4 THEN 1 ELSE 0 END), 0) AS presence_h04,
    COALESCE(MAX(CASE WHEN matched.hour_index = 5 THEN 1 ELSE 0 END), 0) AS presence_h05,
    COALESCE(MAX(CASE WHEN matched.hour_index = 6 THEN 1 ELSE 0 END), 0) AS presence_h06,
    COALESCE(MAX(CASE WHEN matched.hour_index = 7 THEN 1 ELSE 0 END), 0) AS presence_h07,
    COALESCE(MAX(CASE WHEN matched.hour_index = 8 THEN 1 ELSE 0 END), 0) AS presence_h08,
    COALESCE(MAX(CASE WHEN matched.hour_index = 9 THEN 1 ELSE 0 END), 0) AS presence_h09
FROM telco_audit_requests AS requests
LEFT JOIN (
    SELECT
        events.subscriber_id,
        events.patient_link_key,
        cell_map.hospital_id,
        CASE
            WHEN HOUR(events.location_timestamp) >= 22
                THEN DATE(events.location_timestamp)
            ELSE DATE(events.location_timestamp - INTERVAL 1 DAY)
        END AS audit_date,
        CASE
            WHEN HOUR(events.location_timestamp) >= 22
                THEN HOUR(events.location_timestamp) - 22
            ELSE HOUR(events.location_timestamp) + 2
        END AS hour_index
    FROM telco_location_events AS events
    JOIN telco_hospital_cell_map AS cell_map
      ON events.nCGI = cell_map.nCGI
     AND (cell_map.TAI IS NULL OR events.TAI = cell_map.TAI)
     AND events.location_timestamp >= cell_map.valid_from
     AND (
        cell_map.valid_to IS NULL
        OR events.location_timestamp < cell_map.valid_to
     )
    WHERE HOUR(events.location_timestamp) >= 22
       OR HOUR(events.location_timestamp) < 8
) AS matched
  ON requests.subscriber_id = matched.subscriber_id
 AND requests.patient_link_key = matched.patient_link_key
 AND requests.hospital_id = matched.hospital_id
 AND requests.audit_date = matched.audit_date
GROUP BY
    requests.subscriber_id,
    requests.patient_link_key,
    requests.hospital_id,
    requests.audit_date;

GRANT SELECT ON mini_mpc_telco_test.v_telco_hourly_presence
TO 'mini_mpc_telco_reader'@'%';
