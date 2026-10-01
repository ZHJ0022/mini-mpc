"""稽核筛选、模型约束及等级输出。 / Audit selection, model constraints and level output."""

from datetime import date, datetime

import pytest

from mini_mpc.applications.features import (
    TelcoPresenceFeatures,
    derive_clinical_need_score,
    derive_telco_presence_features,
    validate_feature_value,
)
from mini_mpc.applications.insurance_audit import (
    AuditTask,
    InsurerInpatientRecord,
    RiskModelConfig,
    audit_window_start,
    classify_risk_score,
    compute_insurance_audit_result,
    create_audit_task_from_record,
    select_audit_candidates,
)
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from mini_mpc.runtime.backend import InMemoryShamirBackend


def _submitted_backend(session_id: str, values: tuple[int, int, int]) -> InMemoryShamirBackend:
    """模拟运营商和医院分别提交各自特征的 shares。

    Simulate data holders submitting their own feature shares before audit.
    """

    backend = InMemoryShamirBackend()
    names = ("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score")
    for name, value in zip(names, values, strict=True):
        for share in share_secret(value, threshold=2, num_parties=3, modulus=251):
            backend.submit_feature_share(session_id, name, share)
    return backend


def test_derive_telco_presence_features_counts_absence_and_streak():
    features = derive_telco_presence_features([1, 1, 0, 0, 0, 1, 1, 0, 0, 1])

    assert features.telco_absence_hours == 5
    assert features.telco_max_absence_streak == 3


def test_derive_telco_presence_features_rejects_invalid_sequence():
    with pytest.raises(ValueError):
        derive_telco_presence_features([1, 0])

    with pytest.raises(ValueError):
        derive_telco_presence_features([1, 1, 2, 0, 0, 1, 1, 0, 0, 1])


def test_derive_clinical_need_score_uses_configured_score_table():
    score = derive_clinical_need_score(
        disease_category="respiratory",
        severity_level="moderate",
        score_table={("respiratory", "moderate"): 6},
    )

    assert score == 6


def test_derive_clinical_need_score_rejects_unknown_or_invalid_score():
    with pytest.raises(ValueError):
        derive_clinical_need_score(
            disease_category="unknown",
            severity_level="moderate",
            score_table={("respiratory", "moderate"): 6},
        )

    with pytest.raises(ValueError):
        derive_clinical_need_score(
            disease_category="respiratory",
            severity_level="severe",
            score_table={("respiratory", "severe"): 12},
        )


def test_compute_insurance_audit_result_discloses_result_to_insurer():
    task = AuditTask(
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    )
    backend = _submitted_backend(task.audit_id, (5, 3, 6))
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


def test_compute_insurance_audit_result_uses_backend_interface():
    class FakeBackend:
        def __init__(self):
            self.submitted_ids = None
            self.disclosed_values = []

        def compute_submitted_level_shares(
            self,
            session_id,
            input_secret_ids,
            weights,
            *,
            thresholds,
            level_codes,
            score_range,
            threshold,
            num_parties,
            modulus,
        ):
            self.submitted_ids = (tuple(input_secret_ids), tuple(weights), session_id)
            return share_secret(level_codes["medium"], threshold, num_parties, modulus)

        def disclose_to_receiver(self, output_shares, policy, receiver_id):
            disclosed = reconstruct_secret(output_shares[: policy.min_shares])
            self.disclosed_values.append(disclosed.value)
            return disclosed

    backend = FakeBackend()

    result = compute_insurance_audit_result(
        AuditTask(
            audit_id="audit-001",
            audit_case_id="audit-case-001",
            patient_link_key="patient-link-001",
            hospital_id="hospital-001",
            audit_date=date(2026, 8, 28),
        ),
        threshold=2,
        num_parties=3,
        modulus=251,
        receiver_id="insurer",
        backend=backend,
    )

    assert result.risk_level == "medium"
    assert backend.submitted_ids == (("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score"), (6, 4, 3), "audit-001")
    assert backend.disclosed_values == [2]


def test_data_holder_rejects_out_of_range_feature_before_sharing():
    # 明文特征只在数据方本地校验，医保业务入口不会接收该值。
    # The data holder validates plaintext locally; the insurer entry never sees it.
    with pytest.raises(ValueError):
        validate_feature_value("telco_absence_hours", 11)


def test_compute_insurance_audit_result_rejects_modulus_that_can_wrap_score():
    task = AuditTask(
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    )
    with pytest.raises(ValueError):
        compute_insurance_audit_result(
            task,
            threshold=2,
            num_parties=3,
            modulus=127,
            receiver_id="insurer",
            backend=_submitted_backend(task.audit_id, (5, 3, 6)),
        )


def test_select_audit_candidates_keeps_only_current_stays_admitted_before_window():
    audit_date = date(2026, 8, 28)
    eligible = InsurerInpatientRecord(
        insured_person_id="insured-001",
        inpatient_record_id="inpatient-001",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 21, 30),
        is_currently_inpatient=True,
        inpatient_status="emergency",
    )
    completed_stay = InsurerInpatientRecord(
        insured_person_id="insured-002",
        inpatient_record_id="inpatient-002",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 20, 0),
        is_currently_inpatient=False,
        inpatient_status="routine",
    )
    late_admission = InsurerInpatientRecord(
        insured_person_id="insured-003",
        inpatient_record_id="inpatient-003",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 22, 30),
        is_currently_inpatient=True,
        inpatient_status="routine",
    )

    candidates = select_audit_candidates(
        [eligible, completed_stay, late_admission],
        audit_date,
    )

    assert candidates == [eligible]


def test_create_audit_task_from_record_uses_task_management_case_id():
    record = InsurerInpatientRecord(
        insured_person_id="insured-001",
        inpatient_record_id="inpatient-record-from-database",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 21, 30),
        is_currently_inpatient=True,
        inpatient_status="routine",
    )

    task = create_audit_task_from_record(
        record,
        audit_id="audit-001",
        audit_case_id="audit-case-from-task-system",
        patient_link_key="patient-link-001",
        audit_date=date(2026, 8, 28),
    )

    assert task.audit_id == "audit-001"
    assert task.audit_case_id == "audit-case-from-task-system"
    assert task.audit_case_id != record.inpatient_record_id
    assert task.hospital_id == "hospital-001"


def test_create_audit_task_from_record_rejects_ineligible_record():
    record = InsurerInpatientRecord(
        insured_person_id="insured-001",
        inpatient_record_id="inpatient-001",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 23, 0),
        is_currently_inpatient=True,
        inpatient_status="routine",
    )

    with pytest.raises(ValueError):
        create_audit_task_from_record(
            record,
            audit_id="audit-001",
            audit_case_id="audit-case-001",
            patient_link_key="patient-link-001",
            audit_date=date(2026, 8, 28),
        )


def test_audit_window_start_uses_fixed_overnight_policy():
    assert audit_window_start(date(2026, 8, 28)) == datetime(2026, 8, 28, 22, 0)


def test_risk_model_config_rejects_mismatched_features_and_weights():
    with pytest.raises(ValueError):
        RiskModelConfig(
            feature_names=("telco_absence_hours", "clinical_need_score"),
            weights=(6,),
            thresholds={"high": 80, "medium": 45, "low": 20},
        )


def test_classify_risk_score_uses_configured_thresholds():
    thresholds = {"high": 80, "medium": 45, "low": 20}

    assert classify_risk_score(80, thresholds) == "high"
    assert classify_risk_score(45, thresholds) == "medium"
    assert classify_risk_score(20, thresholds) == "low"
    assert classify_risk_score(19, thresholds) == "none"


@pytest.mark.parametrize("weights,error", [
    ((-6, 4, 3), ValueError), ((True, 4, 3), TypeError), ((6.0, 4, 3), TypeError),
])
def test_risk_model_rejects_unsupported_business_weights(weights, error):
    with pytest.raises(error):
        RiskModelConfig(
            ("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score"),
            weights, {"high": 80, "medium": 45, "low": 20},
        )


@pytest.mark.parametrize("thresholds,error", [
    ({"high": True, "medium": 45, "low": 20}, TypeError),
    ({"high": 40, "medium": 45, "low": 20}, ValueError),
    ({"high": 80, "medium": 45, "low": -1}, ValueError),
    ({"high": 131, "medium": 45, "low": 20}, ValueError),
])
def test_risk_model_rejects_invalid_thresholds(thresholds, error):
    with pytest.raises(error):
        RiskModelConfig(
            ("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score"),
            (6, 4, 3), thresholds,
        )


@pytest.mark.parametrize("names", [
    ("telco_absence_hours", "unknown", "clinical_need_score"),
    ("telco_absence_hours", "telco_absence_hours", "clinical_need_score"),
])
def test_risk_model_rejects_unknown_or_duplicate_features(names):
    with pytest.raises(ValueError):
        RiskModelConfig(names, (6, 4, 3), {"high": 80, "medium": 45, "low": 20})
