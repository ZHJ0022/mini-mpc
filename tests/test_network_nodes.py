"""机构特征提交与远程协议边界。 / Organization submissions and remote protocol boundaries."""

from datetime import date, datetime

import pytest

from mini_mpc.applications.nodes import (
    HospitalNode,
    InsurerNode,
    SharingConfig,
    TelcoNode,
    remote_classify_level_shares,
    submit_feature_shares,
)
from mini_mpc.runtime.remote_protocol import (
    remote_collect_shares, remote_multiply_shares, remote_evaluate_public_polynomial,
)
from mini_mpc.math.field import FieldElement
from mini_mpc.runtime.party import MPCParty
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from network_test_support import RunningPartyClients


def test_data_nodes_submit_feature_shares_to_remote_mpc_parties(audit_services):
    services = audit_services
    session_id = "node-submit"
    plan = services.register(session_id)
    # 数据方用各自证书提交，医保仅调度计算。
    # Providers submit with their own certificates; the insurer only coordinates.
    TelcoNode().submit_feature_shares(_telco_medium_risk_row(), services.sharing("telco", session_id))
    HospitalNode().submit_feature_shares({"clinical_need_score": 6}, services.sharing("hospital", session_id))
    for client in services.role_clients["insurer"]:
        client.compute_weighted_sum(session_id, plan.feature_ids, (6, 4, 3), "weighted_sum")
    # 仅测试代码从服务内部验证分数；正式网络接口禁止读取它。
    # Inspect the score internally for testing; the deployed API never exposes it.
    shares = [server.party.get_share(session_id, "weighted_sum") for server in services.servers]
    assert reconstruct_secret(shares) == FieldElement(60, 251)
    assert [share.party_id for share in shares] == [1, 2, 3]


def test_party_service_rejects_share_for_the_wrong_party(audit_services):
    session_id = "node-wrong-party"
    plan = audit_services.register(session_id)
    share = share_secret(4, 2, 3, 251)[1]
    with pytest.raises(RuntimeError, match="does not belong to this party"):
        audit_services.role_clients["telco"][0].submit_share(session_id, plan.feature_ids[0], share)
    assert not any(key[0] == session_id for key in audit_services.servers[0].party.store.keys())


def test_remote_multiplication_uses_network_bgw_reshares():
    # 测试网络化 BGW 乘法是否通过 reshare 消息得到 reduced product shares。
    # Test that networked BGW multiplication returns reduced product shares through reshare messages.
    with RunningPartyClients(3) as party_clients:
        sharing = SharingConfig(
            session_id="multiply-001",
            threshold=2,
            num_parties=3,
            modulus=251,
            party_clients=tuple(party_clients),
        )
        submit_feature_shares("left", 7, sharing)
        submit_feature_shares("right", 8, sharing)

        product_shares = remote_multiply_shares(
            sharing,
            "left",
            "right",
            "product",
        )

        assert reconstruct_secret(product_shares[:2]) == FieldElement(56, 251)
        for client in party_clients:
            share = client.get_share("multiply-001", "product")
            assert share.party_id == client.endpoint.party_id


def test_remote_public_polynomial_evaluates_over_shared_value():
    # 测试远程公开多项式求值是否只重构最终结果，不需要公开输入值。
    # Test that remote public-polynomial evaluation reconstructs only the final result, not the input value.
    with RunningPartyClients(3) as party_clients:
        sharing = SharingConfig(
            session_id="poly-001",
            threshold=2,
            num_parties=3,
            modulus=251,
            party_clients=tuple(party_clients),
        )
        submit_feature_shares("score", 6, sharing)

        output_shares = remote_evaluate_public_polynomial(
            sharing,
            "score",
            (3, 2, 4),
            "poly_output",
        )

        assert reconstruct_secret(output_shares[:2]) == FieldElement(159, 251)


def test_remote_classification_keeps_score_shared():
    # 测试网络化等级分类是否在 shared score 上完成，输出仍是 risk_level shares。
    # Test that networked classification runs over a shared score and outputs risk_level shares.
    with RunningPartyClients(3) as party_clients:
        sharing = SharingConfig(
            session_id="classify-001",
            threshold=2,
            num_parties=3,
            modulus=251,
            party_clients=tuple(party_clients),
        )
        submit_feature_shares("risk_score", 6, sharing)

        level_shares = remote_classify_level_shares(
            sharing,
            "risk_score",
            thresholds={"high": 8, "medium": 5, "low": 2},
            score_range=(0, 10),
        )

        assert reconstruct_secret(level_shares[:2]) == FieldElement(2, 251)


def test_insurer_node_creates_task_from_its_own_view_row():
    # 测试医保节点是否只用医保 view 行创建任务，任务 case id 不来自住院记录号。
    # Test that the insurer node creates a task from its own view row and does not use the inpatient record id as case id.
    task = InsurerNode().audit_task_from_view_row(
        {
            "insured_person_id": "insured-001",
            "inpatient_record_id": "inpatient-001",
            "hospital_id": "hospital-001",
            "admission_time": datetime(2026, 8, 28, 21, 30),
            "is_currently_inpatient": 1,
            "inpatient_status": "emergency",
        },
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key="patient-link-001",
        audit_date=date(2026, 8, 28),
    )

    assert task.audit_id == "audit-001"
    assert task.audit_case_id == "audit-case-001"
    assert task.audit_case_id != "inpatient-001"
    assert task.patient_link_key == "patient-link-001"
    assert task.hospital_id == "hospital-001"


def test_nodes_depend_on_transport_interface_not_plain_http():
    # 测试节点层是否只依赖 PartyTransport 接口，为 HTTPS/mTLS 或自定义安全信道预留替换点。
    # Test that node code depends only on PartyTransport, leaving a replacement point for HTTPS/mTLS or custom secure channels.
    transports = tuple(InProcessPartyTransport(party_id) for party_id in range(1, 4))
    sharing = SharingConfig(
        session_id="transport-001",
        threshold=2,
        num_parties=3,
        modulus=251,
        party_clients=transports,
    )

    submit_feature_shares("value", 9, sharing)

    shares = remote_collect_shares(sharing, "value")

    assert reconstruct_secret(shares[:2]) == FieldElement(9, 251)


def _telco_medium_risk_row() -> dict[str, int]:
    """Return a telecom view row that derives score 60 with clinical score 6.

    返回运营商 view 行；配合 clinical score 6 时总分为 60。
    Return a telecom view row; together with clinical score 6, the total score
    is 60.
    """

    return {
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


class InProcessPartyTransport:
    """Minimal in-process transport for feature-routing tests.

    仅实现特征路由测试所需的接口；完整授权与计算使用 HTTPS 服务验证。
    """

    def __init__(self, party_id: int) -> None:
        self.endpoint = MPCPartyEndpoint(
            party_id=party_id,
            host="in-process",
            port=party_id,
            scheme="in-process",
        )
        self.party = MPCParty(party_id)

    def submit_share(self, session_id, secret_id, share):
        self.party.receive_share(session_id, secret_id, share)

    def submit_public_constant(
        self,
        session_id,
        secret_id,
        value,
        *,
        threshold,
        num_parties,
        modulus,
    ):
        raise NotImplementedError("not needed by this interface test")

    def compute_weighted_sum_share(
        self,
        session_id,
        input_secret_ids,
        weights,
        output_secret_id,
    ):
        return self.party.compute_weighted_sum_share(
            session_id,
            tuple(input_secret_ids),
            tuple(weights),
            output_secret_id,
        )

    def distribute_multiplication_reshares(
        self,
        session_id,
        left_secret_id,
        right_secret_id,
        output_secret_id,
        peer_endpoints,
    ):
        raise NotImplementedError("not needed by this interface test")

    def get_share(self, session_id, secret_id):
        return self.party.get_share(session_id, secret_id)

    def health(self):
        return {"status": "ok", "party_id": self.endpoint.party_id}
