"""远程后端计算与仅等级披露。 / Remote computation and level-only disclosure."""

from datetime import date, datetime

import pytest

from mini_mpc.applications.nodes import HospitalNode, InsurerNode, TelcoNode
from mini_mpc.applications.insurance_audit import compute_insurance_audit_result
from mini_mpc.math.field import FieldElement
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy
from mini_mpc.runtime.remote_backend import RemoteShamirBackend
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from network_test_support import RunningPartyClients


def test_remote_backend_weighted_sum_uses_https_party_services():
    with RunningPartyClients(3) as party_clients:
        backend = RemoteShamirBackend(party_clients)

        score_shares = backend.weighted_sum_shares(
            [5, 3, 6],
            [6, 4, 3],
            threshold=2,
            num_parties=3,
            modulus=251,
            session_id="audit-001",
        )

        assert reconstruct_secret(score_shares[:2]) == FieldElement(60, 251)
        assert "audit-001" in backend.sessions


def test_remote_backend_classifies_score_without_reconstructing_it():
    with RunningPartyClients(3) as party_clients:
        backend = RemoteShamirBackend(party_clients)
        score_shares = backend.weighted_sum_shares(
            [5, 3, 6],
            [6, 4, 3],
            threshold=2,
            num_parties=3,
            modulus=251,
            session_id="audit-001",
        )

        level_shares = backend.classify_level_shares(
            score_shares,
            thresholds={"high": 80, "medium": 45, "low": 20},
            level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
            score_range=(0, 130),
            threshold=2,
            num_parties=3,
            modulus=251,
        )

        assert reconstruct_secret(level_shares[:2]) == FieldElement(2, 251)
        with pytest.raises(ValueError):
            reconstruct_secret(level_shares[:1])


def test_remote_backend_integrates_with_insurance_audit_application(monkeypatch, audit_services):
    party_clients = audit_services.role_clients["insurer"]
    audit_services.register("backend-audit")
    backend = RemoteShamirBackend(party_clients)
    fetched_secret_ids = []
    for client in party_clients:
        monkeypatch.setattr(client, "get_share", lambda *args: pytest.fail("intermediate share read"))
        original_get_result_share = client.get_result_share

        def record_get_result_share(session_id, *, original=original_get_result_share):
            fetched_secret_ids.append("risk_level")
            return original(session_id)

        monkeypatch.setattr(client, "get_result_share", record_get_result_share)
    # 运营商与医院节点在本地分享特征，业务入口只引用已提交的 secret ids。
    # Provider nodes share features locally; the business entry uses their secret ids.
    class TelcoReader:
        def read(self, patient_link_key, hospital_id, audit_date):
            assert (patient_link_key, hospital_id, audit_date) == (
                "patient-link-001", "hospital-001", date(2026, 8, 28)
            )
            return {
            "presence_h00": 1, "presence_h01": 1, "presence_h02": 0,
            "presence_h03": 0, "presence_h04": 0, "presence_h05": 1,
            "presence_h06": 1, "presence_h07": 0, "presence_h08": 0,
            "presence_h09": 1,
            }

    class HospitalReader:
        def read(self, patient_link_key, hospital_id):
            assert (patient_link_key, hospital_id) == ("patient-link-001", "hospital-001")
            return {"clinical_need_score": 6}

    class InsurerReader:
        def read(self, inpatient_record_id, hospital_id):
            assert (inpatient_record_id, hospital_id) == ("record-001", "hospital-001")
            return {
                "insured_person_id": "insured-001",
                "inpatient_record_id": "record-001",
                "hospital_id": "hospital-001",
                "admission_time": datetime(2026, 8, 28, 21, 30),
                "is_currently_inpatient": 1,
                "inpatient_status": "emergency",
            }

    assert TelcoNode().submit_from_view(
        TelcoReader(), audit_services.sharing("telco", "backend-audit"),
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    ) is None
    assert HospitalNode().submit_from_view(
        HospitalReader(), audit_services.sharing("hospital", "backend-audit"),
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
    ) is None
    task = InsurerNode().audit_task_from_database(
        InsurerReader(),
        inpatient_record_id="record-001",
        hospital_id="hospital-001",
        audit_id="backend-audit",
        audit_case_id="audit-case-001",
        patient_link_key="patient-link-001",
        audit_date=date(2026, 8, 28),
    )
    result = compute_insurance_audit_result(
        task,
        threshold=2,
        num_parties=3,
        modulus=251,
        receiver_id="insurer",
        backend=backend,
    )

    assert result.risk_level == "medium"
    assert not hasattr(result, "risk_score")
    # 业务调度只读取最终等级 shares，不读取 score 或 Horner 中间 shares。
    # The coordinator fetches only final level shares, never intermediates.
    assert fetched_secret_ids == ["risk_level"] * 3


def test_remote_backend_can_classify_external_score_shares():
    with RunningPartyClients(3) as party_clients:
        backend = RemoteShamirBackend(party_clients)
        score_shares = share_secret(60, threshold=2, num_parties=3, modulus=251)

        level_shares = backend.classify_level_shares(
            score_shares,
            thresholds={"high": 80, "medium": 45, "low": 20},
            level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
            score_range=(0, 130),
            threshold=2,
            num_parties=3,
            modulus=251,
        )

        assert reconstruct_secret(level_shares[:2]) == FieldElement(2, 251)


def test_remote_backend_discloses_only_to_authorized_receiver():
    backend = RemoteShamirBackend(())
    shares = share_secret(2, threshold=2, num_parties=3, modulus=251)
    policy = OutputDisclosurePolicy(receiver_id="insurer", min_shares=2)

    assert backend.disclose_to_receiver(shares, policy, "insurer") == FieldElement(2, 251)
    with pytest.raises(PermissionError):
        backend.disclose_to_receiver(shares, policy, "hospital")


def test_submitted_remote_audit_fails_when_one_provider_input_is_missing(audit_services):
    # 缺少医院输入时，party 本地求和应显式失败，不能输出默认等级。
    # Missing hospital input must fail at party computation, not yield a default level.
    party_clients = audit_services.role_clients["insurer"]
    audit_services.register("missing-001")
    backend = RemoteShamirBackend(party_clients)
    sharing = audit_services.sharing("telco", "missing-001")
    TelcoNode().submit_feature_shares({
        "presence_h00": 1, "presence_h01": 1, "presence_h02": 0,
        "presence_h03": 0, "presence_h04": 0, "presence_h05": 1,
        "presence_h06": 1, "presence_h07": 0, "presence_h08": 0,
        "presence_h09": 1,
    }, sharing)

    with pytest.raises(RuntimeError, match="missing"):
        backend.compute_submitted_level_shares(
            "missing-001",
            ("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score"),
            (6, 4, 3),
            thresholds={"high": 80, "medium": 45, "low": 20},
            level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
            score_range=(0, 130),
            threshold=2, num_parties=3, modulus=251,
        )
