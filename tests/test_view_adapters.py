"""只读视图字段到内部类型的转换。 / Read-only view fields converted to internal types."""

from datetime import datetime

import pytest

from mini_mpc.applications.view_adapters import (
    clinical_need_score_from_view_row,
    clinical_score_input_from_view_row,
    hourly_presence_from_view_row,
    insurer_record_from_view_row,
)
from mini_mpc.applications.features import (
    derive_clinical_need_score,
    derive_telco_presence_features,
)
from mini_mpc.applications.preprocessing import (
    derive_hospital_clinical_need_score_from_view_row,
    derive_telco_features_from_hourly_view,
)
from mini_mpc.applications.insurance_audit import (
    compute_insurance_audit_result,
    create_audit_task_from_record,
    select_audit_candidates,
)
from mini_mpc.runtime.backend import InMemoryShamirBackend
from mini_mpc.sharing.shamir import share_secret


def test_insurer_record_from_view_row_converts_mysql_view_fields():
    row = {
        "insured_person_id": "insured-001",
        "inpatient_record_id": "inpatient-001",
        "hospital_id": "hospital-001",
        "admission_time": datetime(2026, 8, 28, 21, 30),
        "is_currently_inpatient": 1,
        "inpatient_status": "emergency",
    }

    record = insurer_record_from_view_row(row)

    assert record.insured_person_id == "insured-001"
    assert record.inpatient_record_id == "inpatient-001"
    assert record.hospital_id == "hospital-001"
    assert record.admission_time == datetime(2026, 8, 28, 21, 30)
    assert record.is_currently_inpatient is True
    assert record.inpatient_status == "emergency"


def test_insurer_record_from_view_row_rejects_missing_or_invalid_fields():
    complete_row = {
        "insured_person_id": "insured-001",
        "inpatient_record_id": "inpatient-001",
        "hospital_id": "hospital-001",
        "admission_time": datetime(2026, 8, 28, 21, 30),
        "is_currently_inpatient": 1,
        "inpatient_status": "routine",
    }

    missing_hospital = dict(complete_row)
    del missing_hospital["hospital_id"]
    with pytest.raises(KeyError):
        insurer_record_from_view_row(missing_hospital)

    missing_current_flag = dict(complete_row)
    del missing_current_flag["is_currently_inpatient"]
    with pytest.raises(KeyError):
        insurer_record_from_view_row(missing_current_flag)

    invalid_admission_time = dict(complete_row)
    invalid_admission_time["admission_time"] = "2026-08-28 21:30:00"
    with pytest.raises(TypeError):
        insurer_record_from_view_row(invalid_admission_time)


def test_hourly_presence_from_view_row_reads_mysql_like_bit_fields_in_order():
    row = {
        "presence_h00": 1,
        "presence_h01": True,
        "presence_h02": "0",
        "presence_h03": "false",
        "presence_h04": 0,
        "presence_h05": "TRUE",
        "presence_h06": "yes",
        "presence_h07": "NO",
        "presence_h08": False,
        "presence_h09": "1",
    }

    hourly_presence = hourly_presence_from_view_row(row)

    assert hourly_presence == [1, 1, 0, 0, 0, 1, 1, 0, 0, 1]


def test_hourly_presence_from_view_row_rejects_missing_or_non_bit_fields():
    row = {f"presence_h{hour_index:02d}": 1 for hour_index in range(10)}

    missing_hour = dict(row)
    del missing_hour["presence_h09"]
    with pytest.raises(KeyError):
        hourly_presence_from_view_row(missing_hour)

    invalid_bit = dict(row)
    invalid_bit["presence_h03"] = 2
    with pytest.raises(ValueError):
        hourly_presence_from_view_row(invalid_bit)


def test_clinical_score_input_from_view_row_reads_required_hospital_fields():
    row = {
        "patient_link_key": "patient-link-001",
        "hospital_inpatient_record_id": "hospital-record-001",
        "diagnosis_code": "J18.9",
        "disease_category": "respiratory",
        "severity_level": "moderate",
    }

    disease_category, severity_level = clinical_score_input_from_view_row(row)

    assert disease_category == "respiratory"
    assert severity_level == "moderate"


def test_clinical_score_input_from_view_row_rejects_missing_or_invalid_fields():
    row = {
        "disease_category": "respiratory",
        "severity_level": "moderate",
    }

    missing_category = dict(row)
    del missing_category["disease_category"]
    with pytest.raises(KeyError):
        clinical_score_input_from_view_row(missing_category)

    invalid_severity = dict(row)
    invalid_severity["severity_level"] = 2
    with pytest.raises(TypeError):
        clinical_score_input_from_view_row(invalid_severity)


def test_clinical_need_score_from_view_row_reads_hospital_derived_score():
    row = {"clinical_need_score": 6}

    assert clinical_need_score_from_view_row(row) == 6

    with pytest.raises(ValueError):
        clinical_need_score_from_view_row({"clinical_need_score": 11})
    with pytest.raises(TypeError):
        clinical_need_score_from_view_row({"clinical_need_score": "6"})


def test_simulated_mysql_views_feed_audit_task_and_mpc_result():
    audit_date = datetime(2026, 8, 28).date()
    insurer_view_row = {
        "insured_person_id": "insured-001",
        "inpatient_record_id": "insurer-inpatient-001",
        "hospital_id": "hospital-001",
        "admission_time": datetime(2026, 8, 28, 21, 30),
        "is_currently_inpatient": 1,
        "inpatient_status": "emergency",
    }
    telco_hourly_view_row = {
        "subscriber_id": "subscriber-001",
        "hospital_id": "hospital-001",
        "audit_date": audit_date,
        "presence_h00": 1,
        "presence_h01": 1,
        "presence_h02": 0,
        "presence_h03": 0,
        "presence_h04": 0,
        "presence_h05": 1,
        "presence_h06": 1,
        "presence_h07": 0,
        "presence_h08": 0,
        "presence_h09": 1,
    }
    hospital_view_row = {
        "patient_link_key": "patient-link-001",
        "hospital_inpatient_record_id": "hospital-inpatient-001",
        "diagnosis_code": "J18.9",
        "disease_category": "respiratory",
        "severity_level": "moderate",
    }

    insurer_record = insurer_record_from_view_row(insurer_view_row)
    candidates = select_audit_candidates([insurer_record], audit_date)
    task = create_audit_task_from_record(
        candidates[0],
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key=hospital_view_row["patient_link_key"],
        audit_date=audit_date,
    )

    # 运营商 view 行通过预处理入口派生 MPC 特征；当前 view 已经是本地预聚合的小时级结果。
    # The telecom view row derives MPC features through the preprocessing entry; the current view is already a local hourly aggregate.
    telco_features = derive_telco_features_from_hourly_view(telco_hourly_view_row)

    # 医院 view 行通过预处理入口派生临床分数；若 view 未直接提供分数，则使用本地规则表计算。
    # The hospital view row derives the clinical score through preprocessing; if no direct score is present, a local rule table is used.
    clinical_need_score = derive_hospital_clinical_need_score_from_view_row(
        hospital_view_row
    )

    backend = InMemoryShamirBackend()
    # 模拟三方先把本地 view 派生特征分享给各计算方。
    # Simulate data holders sharing view-derived features before the audit.
    for name, value in (
        ("telco_absence_hours", telco_features.telco_absence_hours),
        ("telco_max_absence_streak", telco_features.telco_max_absence_streak),
        ("clinical_need_score", clinical_need_score),
    ):
        for share in share_secret(value, threshold=2, num_parties=3, modulus=251):
            backend.submit_feature_share(task.audit_id, name, share)

    result = compute_insurance_audit_result(
        task,
        threshold=2,
        num_parties=3,
        modulus=251,
        receiver_id="insurer",
        backend=backend,
    )

    assert result.audit_id == "audit-001"
    assert result.audit_case_id == "audit-case-001"
    assert result.risk_level == "medium"
    assert not hasattr(result, "risk_score")


@pytest.mark.parametrize("value", [True, 1.5])
def test_clinical_score_rejects_non_integer_view_values(value):
    with pytest.raises(TypeError, match="integer"):
        clinical_need_score_from_view_row({"clinical_need_score": value})


def test_view_bit_rejects_float_flags():
    row = {f"presence_h{index:02d}": 1 for index in range(10)}
    row["presence_h00"] = 1.0
    with pytest.raises(TypeError, match="bit value"):
        hourly_presence_from_view_row(row)
