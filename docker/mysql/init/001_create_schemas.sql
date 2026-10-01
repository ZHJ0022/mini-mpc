-- 本地合成数据测试环境；固定口令仅用于测试。
-- Local synthetic-data environment; fixed passwords are for tests only.

CREATE DATABASE IF NOT EXISTS mini_mpc_insurer_test;
CREATE DATABASE IF NOT EXISTS mini_mpc_telco_test;
CREATE DATABASE IF NOT EXISTS mini_mpc_hospital_test;

CREATE USER IF NOT EXISTS 'mini_mpc_insurer_reader'@'%' IDENTIFIED BY 'mini_mpc_insurer_reader';
CREATE USER IF NOT EXISTS 'mini_mpc_telco_reader'@'%' IDENTIFIED BY 'mini_mpc_telco_reader';
CREATE USER IF NOT EXISTS 'mini_mpc_hospital_reader'@'%' IDENTIFIED BY 'mini_mpc_hospital_reader';
FLUSH PRIVILEGES;
