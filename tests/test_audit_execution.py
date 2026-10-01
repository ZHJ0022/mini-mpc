"""Full mTLS audit execution, interrupted delivery and secret-free events.

验证正式 mTLS 稽核流程、中断续传及不含秘密值的事件记录。
Verify deployed mTLS audits, resumed delivery and events without secret values.
"""

import json
import logging

import pytest

from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.runtime.remote_backend import RemoteShamirBackend
from mini_mpc.runtime.remote_protocol import submit_feature_shares
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from mini_mpc.sharing.share import Share


def _register(services, session_id):
    return services.register(session_id)


def _compute(services, session_id):
    return RemoteShamirBackend(services.role_clients["insurer"]).compute_submitted_level_shares(
        session_id, services.servers[0].computation_plan.feature_ids, (6, 4, 3),
        thresholds={"high": 80, "medium": 45, "low": 20},
        level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
        score_range=(0, 130), threshold=2, num_parties=3, modulus=251,
    )


@pytest.mark.parametrize("session_id,values,level", [
    ("none", (0, 0, 0), 0), ("low", (3, 1, 0), 1),
    ("medium", (5, 3, 6), 2), ("high", (10, 10, 10), 3),
])
def test_full_mtls_audit_retries_preserve_only_final_level(
    audit_services, session_id, values, level, monkeypatch
):
    services = audit_services
    plan = _register(services, session_id)
    for feature, value, identity in zip(plan.feature_ids, values, ("telco", "telco", "hospital"), strict=True):
        submit_feature_shares(feature, value, services.sharing(identity, session_id))

    # 医保业务不能调用任意 share 读取；若编排误用该方法，测试立即失败。
    # Fail immediately if business orchestration tries to fetch arbitrary intermediate shares.
    for client in services.role_clients["insurer"]:
        monkeypatch.setattr(client, "get_share", lambda *args: pytest.fail("intermediate share read"))

    if session_id == "medium":
        original = MPCPartyHttpClient.submit_reshare
        first_multiply = next(step for step in plan.steps if step.kind == "multiply")
        attempts = []

        def lose_response(client, session, secret_id, share):
            original(client, session, secret_id, share)
            if session == session_id and secret_id == f"{first_multiply.output_id}__bgw_reshare_from_1":
                attempts.append(share)
                if share.party_id == 2 and len(attempts) == 2:
                    # 已写入接收方后模拟响应丢失；重试必须发送同一份额。
                    # Lose the response after storage; retry must send the identical share.
                    raise OSError("private peer error must be redacted")

        monkeypatch.setattr(MPCPartyHttpClient, "submit_reshare", lose_response)
        with pytest.raises(RuntimeError, match="peer delivery failed"):
            _compute(services, session_id)

    outputs = _compute(services, session_id)
    assert reconstruct_secret(outputs).value == level
    if session_id == "medium":
        assert [share.party_id for share in attempts] == [1, 2, 2, 3]
        assert attempts[1] == attempts[2]
        # 重新构造 backend 并重放完整编排，最终份额必须逐一相同。
        # Recreate the backend and replay the whole workflow; final shares stay identical.
        assert _compute(services, session_id) == outputs
    for identity in ("telco", "hospital", "party-1"):
        with pytest.raises(RuntimeError, match="not permitted"):
            services.role_clients[identity][0].get_result_share(session_id)
    if session_id == "high":
        # 已完成任务的结果领取和计算重试也必须检查有效期。
        # Completed tasks still check expiry for result reads and computation retries.
        expires_at = services.servers[0].tasks.require(session_id).expires_at
        monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: expires_at)
        with pytest.raises(RuntimeError, match="expired"):
            services.role_clients["insurer"][0].get_result_share(session_id)
        with pytest.raises(RuntimeError, match="expired"):
            _compute(services, session_id)


def test_service_order_expiry_cross_session_and_event_allowlist(audit_services, monkeypatch, caplog):
    services = audit_services
    session_id = "event-audit"
    caplog.set_level(logging.INFO, logger="mini_mpc.security")
    plan = _register(services, session_id)
    insurer = services.role_clients["insurer"][0]
    telco = services.role_clients["telco"][0]
    party = services.role_clients["party-1"][0]
    first_constant = plan.steps[1]
    first_multiply = plan.steps[2]
    initial_keys = services.servers[0].party.store.keys()
    with pytest.raises(RuntimeError, match="out of order"):
        insurer.submit_public_constant(session_id, first_constant.output_id, first_constant.value,
                                       threshold=2, num_parties=3, modulus=251)
    with pytest.raises(RuntimeError, match="missing"):
        insurer.compute_weighted_sum(session_id, plan.feature_ids, (6, 4, 3), "weighted_sum")
    with pytest.raises(RuntimeError, match="not ready"):
        insurer.get_result_share(session_id)
    with pytest.raises(RuntimeError, match="outside"):
        party.submit_reshare(session_id, f"{first_multiply.output_id}__bgw_reshare_from_1",
                             share_secret(2, 2, 3, 251)[0])
    assert services.servers[0].party.store.keys() == initial_keys

    share = share_secret(5, 2, 3, 251)[0]
    telco.submit_share(session_id, plan.feature_ids[0], share)
    telco.submit_share(session_id, plan.feature_ids[0], share)
    with pytest.raises(RuntimeError, match="conflicting"):
        telco.submit_share(session_id, plan.feature_ids[0], Share(1, share.value + 1, 2, 3))
    with pytest.raises(RuntimeError, match="not registered"):
        telco.submit_share("unregistered-cross-session", plan.feature_ids[0], share)
    with pytest.raises(RuntimeError, match="disabled"):
        insurer.get_share(session_id, plan.feature_ids[0])
    manifest = services.servers[0].tasks.require(session_id)
    expires_at = manifest.expires_at
    monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: expires_at)
    for request in (
        lambda: insurer.register_task(manifest),
        lambda: telco.submit_share(session_id, plan.feature_ids[0], share),
        lambda: insurer.compute_weighted_sum(session_id, plan.feature_ids, (6, 4, 3), "weighted_sum"),
        lambda: insurer.get_result_share(session_id),
    ):
        with pytest.raises(RuntimeError, match="expired"):
            request()

    events = [json.loads(record.message) for record in caplog.records if record.name == "mini_mpc.security"]
    assert events
    expected_keys = {"timestamp", "party_id", "session_id", "caller_identity", "operation", "decision", "error_code"}
    assert all(set(event) == expected_keys for event in events)
    assert any(event["decision"] == "retry" for event in events)
    assert any(event["operation"] == "read_share"
               and event["session_id"] == session_id
               and event["decision"] == "denied" for event in events)
    assert {"wrong_order", "missing_input", "result_not_ready", "share_conflict", "task_expired"} <= {
        event["error_code"] for event in events
    }
    # 未授权请求只能产生拒绝事件，日志中不能包含请求体、份额或私钥。
    # Rejections produce metadata only, never request bodies, shares or private keys.
    assert not any(word in caplog.text for word in ("FieldElement", "PRIVATE KEY", '"share":', '"value":'))



def test_peer_failure_records_only_public_error_metadata(audit_services, monkeypatch, caplog):
    services = audit_services
    session_id = "peer-event-audit"
    caplog.set_level(logging.INFO, logger="mini_mpc.security")
    plan = _register(services, session_id)
    for feature, identity in zip(plan.feature_ids, ("telco", "telco", "hospital"), strict=True):
        submit_feature_shares(feature, 1, services.sharing(identity, session_id))
    insurer = services.role_clients["insurer"][0]
    insurer.compute_weighted_sum(session_id, plan.feature_ids, (6, 4, 3), "weighted_sum")
    constant, multiply = plan.steps[1:3]
    insurer.submit_public_constant(session_id, constant.output_id, constant.value,
                                   threshold=2, num_parties=3, modulus=251)

    def fail(*args):
        # 原异常模拟含有秘密或路径的信息；HTTP 和事件只能返回固定错误类别。
        # Simulate sensitive exception text; HTTP and events must expose only a fixed error.
        raise OSError("PRIVATE KEY private-feature=9 confidential/path")

    monkeypatch.setattr(MPCPartyHttpClient, "submit_reshare", fail)
    with pytest.raises(RuntimeError, match="peer delivery failed") as failure:
        insurer.distribute_multiplication_reshares(
            session_id, *multiply.input_ids, multiply.output_id,
            services.servers[0].peer_endpoints,
        )
    events = [json.loads(record.message) for record in caplog.records if record.name == "mini_mpc.security"]
    assert any(event["error_code"] == "peer_unavailable"
               and event["session_id"] == session_id
               and event["caller_identity"] == "insurer"
               and event["decision"] == "denied" for event in events)
    assert "PRIVATE KEY" not in str(failure.value) + caplog.text
    assert "private-feature" not in str(failure.value) + caplog.text
    assert "confidential/path" not in str(failure.value) + caplog.text
