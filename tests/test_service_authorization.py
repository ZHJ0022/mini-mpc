"""正式服务的拒绝矩阵。 / Rejection matrix for deployed services."""

import json
import logging

import pytest

from mini_mpc.runtime.remote_protocol import submit_feature_shares
from mini_mpc.runtime.serde import share_to_dict
from mini_mpc.sharing.shamir import share_secret, reconstruct_secret
from network_test_support import request_json


# case 决定变更的边界；每行同时固定 HTTP 状态和事件错误码。
# Each case changes one boundary and fixes the expected HTTP status and event code.
CASES = [
    ("register-role", "telco", 403, "permission_denied"),
    ("register-model", "insurer", 403, "permission_denied"),
    ("register-conflict", "insurer", 400, "invalid_request"),
    ("register-receiver", "insurer", 400, "invalid_request"),
    ("input-insurer", "insurer", 403, "permission_denied"),
    ("input-hospital", "hospital", 403, "permission_denied"),
    ("input-telco", "telco", 403, "permission_denied"),
    ("input-unknown", "telco", 403, "permission_denied"),
    ("input-owner", "telco", 400, "invalid_request"),
    ("input-modulus", "telco", 400, "invalid_request"),
    ("input-conflict", "telco", 409, "share_conflict"),
    ("input-unregistered", "telco", 404, "not_found"),
    ("input-bool-party", "telco", 400, "invalid_request"),
    ("input-bool-value", "telco", 400, "invalid_request"),
    ("sum-role", "telco", 403, "permission_denied"),
    ("sum-weights", "insurer", 403, "permission_denied"),
    ("sum-output", "insurer", 403, "permission_denied"),
    ("sum-inputs", "insurer", 403, "permission_denied"),
    ("sum-disclosure", "insurer", 403, "permission_denied"),
    ("sum-missing", "insurer", 409, "missing_input"),
    ("sum-bool", "insurer", 400, "invalid_request"),
    ("constant-value", "insurer", 403, "permission_denied"),
    ("constant-order", "insurer", 409, "wrong_order"),
    ("constant-bool", "insurer", 400, "invalid_request"),
    ("peer-address", "insurer", 403, "permission_denied"),
    ("reshare-role", "telco", 403, "permission_denied"),
    ("reshare-sender", "party-1", 403, "permission_denied"),
    ("reshare-plan", "party-1", 403, "permission_denied"),
    ("reshare-order", "party-1", 409, "wrong_order"),
    ("result-role", "hospital", 403, "permission_denied"),
    ("result-injected", "insurer", 409, "result_not_ready"),
    ("result-pending", "insurer", 409, "result_not_ready"),
    ("result-query", "insurer", 400, "invalid_request"),
    ("result-unregistered", "insurer", 404, "not_found"),
    ("read-share", "insurer", 403, "permission_denied"),
    ("expired", "insurer", 403, "task_expired"),
]


def _snapshot(services, session_id):
    # 比较份额、清单及有效执行进度；尚未分配的进度等价于初始状态。
    # Compare shares, manifests and effective progress; unallocated progress is initial state.
    snapshots = []
    for server in services.servers:
        progress = server.executor._sessions.get(session_id)
        snapshots.append((
            tuple((key, server.party.get_share(*key)) for key in server.party.store.keys()),
            dict(server.tasks._tasks),
            (0, False, None, frozenset()) if progress is None else (
                progress.next_step_index, progress.sending, progress.reshares,
                frozenset(progress.acknowledged_peers),
            ),
        ))
    return snapshots


@pytest.mark.parametrize("case,identity,status,code", CASES, ids=[row[0] for row in CASES])
def test_rejected_requests_preserve_state_and_emit_safe_events(
    audit_services, case, identity, status, code, caplog, monkeypatch
):
    services = audit_services
    session_id = "deny-" + case
    plan = services.register(session_id)
    server = services.servers[0]
    manifest = server.tasks.require(session_id)
    constant = next(step for step in plan.steps if step.kind == "constant")
    multiply = next(step for step in plan.steps if step.kind == "multiply")
    share = share_secret(4, 2, 3, 251)[0]
    payload = {"session_id": session_id}
    method = "POST"
    if case.startswith("register"):
        path = "/sessions"
        payload = manifest.to_payload()
        if case == "register-model": payload["model_version"] = "forged"
        if case == "register-conflict": payload["expires_at"] += 1
        if case == "register-receiver": payload["receiver_id"] = "hospital"
    elif case.startswith("input"):
        path = "/shares"
        payload.update(secret_id=plan.feature_ids[0], share=share_to_dict(share))
        if case == "input-telco": payload["secret_id"] = plan.feature_ids[2]
        if case == "input-unknown": payload["secret_id"] = "unregistered_input"
        if case == "input-owner": payload["share"]["party_id"] = 2
        if case == "input-modulus":
            payload["share"]["modulus"] = 257
            # 错误公开参数必须在份额构造前拒绝，避免先执行数学校验。
            # Reject mismatched parameters before constructing a field element.
            def unexpected_parse(payload):
                raise AssertionError("parsed an unregistered modulus")
            monkeypatch.setattr(
                "mini_mpc.network.http_server.share_from_dict", unexpected_parse
            )
        if case == "input-bool-party": payload["share"]["party_id"] = True
        if case == "input-bool-value": payload["share"]["value"] = True
        if case == "input-unregistered": payload["session_id"] = "unknown-" + session_id
        if case == "input-conflict":
            services.role_clients["telco"][0].submit_share(session_id, plan.feature_ids[0], share)
            payload["share"]["value"] = (share.value.value + 1) % 251
    elif case.startswith("sum"):
        path = "/compute/weighted-sum"
        payload.update(input_secret_ids=list(plan.feature_ids), weights=[6, 4, 3],
                       output_secret_id="weighted_sum", return_share=False)
        if case == "sum-weights": payload["weights"] = [1, 0, 0]
        if case == "sum-output": payload["output_secret_id"] = "unplanned_output"
        if case == "sum-inputs": payload["input_secret_ids"].reverse()
        if case == "sum-disclosure": payload["return_share"] = True
        if case == "sum-bool": payload["weights"][0] = True
    elif case.startswith("constant"):
        path = "/shares/public"
        payload.update(secret_id=constant.output_id, value=constant.value,
                       threshold=2, num_parties=3, modulus=251)
        if case == "constant-value": payload["value"] += 1
        if case == "constant-bool": payload["value"] = True
    elif case == "peer-address":
        path = "/compute/multiply/distribute-reshares"
        payload.update(left_secret_id=multiply.input_ids[0], right_secret_id=multiply.input_ids[1],
                       output_secret_id=multiply.output_id, peer_endpoints=[
                           {"party_id": peer.party_id, "host": peer.host, "port": peer.port + 1,
                            "scheme": peer.scheme} for peer in server.peer_endpoints])
    elif case.startswith("reshare"):
        path = "/shares/reshares"
        secret_id = multiply.output_id + "__bgw_reshare_from_1"
        if case == "reshare-sender": secret_id = multiply.output_id + "__bgw_reshare_from_2"
        if case == "reshare-plan": secret_id = "forged__bgw_reshare_from_1"
        payload.update(secret_id=secret_id, share=share_to_dict(share))
    else:
        method = "GET"
        path = "/results?session_id=" + session_id
        if case == "read-share": path = "/shares?session_id=" + session_id + "&secret_id=weighted_sum"
        if case == "result-query": path += "&secret_id=weighted_sum"
        if case == "result-unregistered": path = "/results?session_id=unknown-" + session_id
        if case == "expired":
            monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: manifest.expires_at)
        payload = None
    if case == "result-injected":
        # 存在等级份额仍不代表计划已完成。 / A stored level share does not complete the plan.
        server.party.receive_share(session_id, "risk_level", share)
    before = _snapshot(services, session_id)
    caplog.set_level(logging.INFO, logger="mini_mpc.security")
    actual_status, response = request_json(services.role_clients[identity][0], method, path, payload)
    assert (actual_status, response["error_code"]) == (status, code)
    assert "share" not in response
    assert _snapshot(services, session_id) == before
    events = [json.loads(record.message) for record in caplog.records if record.name == "mini_mpc.security"]
    assert len(events) == 1
    assert set(events[0]) == {"timestamp", "party_id", "session_id", "caller_identity",
                              "operation", "decision", "error_code"}
    assert events[0]["caller_identity"] == identity
    assert (events[0]["decision"], events[0]["error_code"]) == ("denied", code)


def test_registered_sessions_keep_inputs_and_progress_separate(audit_services):
    services = audit_services
    sessions = ("isolation-a", "isolation-b")
    values = ((1, 2, 3), (8, 7, 6))
    for session_id, inputs in zip(sessions, values, strict=True):
        plan = services.register(session_id)
        for feature, value, identity in zip(plan.feature_ids, inputs, ("telco", "telco", "hospital"), strict=True):
            submit_feature_shares(feature, value, services.sharing(identity, session_id))
    before_b = _snapshot(services, sessions[1])
    for client in services.role_clients["insurer"]:
        client.compute_weighted_sum(sessions[0], plan.feature_ids, (6, 4, 3), "weighted_sum")
    # 第一会话完成一步，第二会话不能领取其结果或复用其进度。
    # Advancing the first session cannot make the second session's result ready.
    for server, before in zip(services.servers, before_b, strict=True):
        assert server.executor._sessions[sessions[1]].next_step_index == before[2][0]
        assert all(key != (sessions[1], "weighted_sum") for key in server.party.store.keys())
    with pytest.raises(RuntimeError, match="not ready"):
        services.role_clients["insurer"][0].get_result_share(sessions[1])
    for client in services.role_clients["insurer"]:
        client.compute_weighted_sum(sessions[1], plan.feature_ids, (6, 4, 3), "weighted_sum")
    scores = [reconstruct_secret([server.party.get_share(session, "weighted_sum")
                                 for server in services.servers]).value for session in sessions]
    assert scores == [23, 94]
