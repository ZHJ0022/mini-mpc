"""CLI 编排使用真实 mTLS，只替换数据库读取。 / Real mTLS CLI with stubbed DB reads."""

import json
import logging
import time
from datetime import datetime

import pytest

from mini_mpc.applications.audit_cli import main


@pytest.fixture
def cli_args(tmp_path, audit_services, monkeypatch):
    configs = {}
    for identity in ("insurer", "telco", "hospital"):
        clients = audit_services.role_clients[identity]
        tls = clients[0].tls_config
        path = tmp_path / (identity + ".json")
        path.write_text(json.dumps({
            "threshold": 2, "num_parties": 3, "modulus": 251,
            "endpoints": [{"party_id": client.endpoint.party_id, "host": client.endpoint.host,
                           "port": client.endpoint.port} for client in clients],
            "tls": {"cafile": tls.cafile, "certfile": tls.certfile, "keyfile": tls.keyfile},
        }))
        configs[identity] = path
        prefix = "MINI_MPC_" + identity.upper() + "_MYSQL_"
        monkeypatch.setenv(prefix + "USER", identity + "_reader")
        monkeypatch.setenv(prefix + "PASSWORD", "private-db-password")

    def fetch(config, database, query, params):
        identity = database.removeprefix("mini_mpc_").removesuffix("_test")
        # reader 的本方账号是数据库边界；不把其他方凭据用于查询。
        # Readers use their own account at the database boundary.
        assert config.user == identity + "_reader"
        assert config.password == "private-db-password"
        if identity == "telco":
            return {f"presence_h{index:02}": value for index, value in enumerate(
                (1, 1, 0, 0, 0, 1, 1, 0, 0, 1))}
        if identity == "hospital":
            return {"clinical_need_score": 6}
        return {"insured_person_id": "private-insured-person", "inpatient_record_id": "record-001",
                "hospital_id": "hospital-001", "admission_time": datetime(2026, 8, 28, 21, 30),
                "is_currently_inpatient": 1, "inpatient_status": "emergency"}

    monkeypatch.setattr("mini_mpc.applications.view_readers._fetch_one", fetch)

    def args(role, session_id, *, certificate=None, expires_at=None):
        identity = "insurer" if role == "insurer-register" else role
        argv = [role, "--client-config", str(configs[certificate or identity]),
                "--session-id", session_id, "--patient-link-key", "private-patient-link",
                "--hospital-id", "hospital-001", "--audit-date", "2026-08-28"]
        if identity == "insurer":
            argv += ["--inpatient-record-id", "record-001", "--audit-case-id", "case-001"]
        if role == "insurer-register":
            argv += ["--expires-at", str(expires_at or int(time.time()) + 3600)]
        return argv
    return args


def test_cli_complete_audit_prints_only_status_and_level(cli_args, capsys):
    session_id = "cli-complete"
    for role, output in (("insurer-register", "status=registered"), ("telco", "status=submitted"),
                         ("hospital", "status=submitted"), ("insurer", "risk_level=medium")):
        assert main(cli_args(role, session_id)) == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == output
        assert captured.err == ""


@pytest.mark.parametrize("case,role,certificate,message,code", [
    ("register-spoof", "insurer-register", "telco", "not permitted", "permission_denied"),
    ("provider-spoof", "telco", "hospital", "not permitted", "permission_denied"),
    ("insurer-spoof", "insurer", "telco", "not permitted", "permission_denied"),
    ("unregistered", "telco", "telco", "not registered", "not_found"),
    ("missing-input", "insurer", "insurer", "missing", "missing_input"),
    ("expired", "insurer", "insurer", "expired", "task_expired"),
])
def test_cli_failure_has_no_success_output(
    audit_services, cli_args, capsys, caplog, monkeypatch, case, role, certificate, message, code
):
    session_id = "cli-deny-" + case
    if case not in ("unregistered", "register-spoof"):
        audit_services.register(session_id)
    if case == "expired":
        expiry = audit_services.servers[0].tasks.require(session_id).expires_at
        monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: expiry)
    keys = [server.party.store.keys() for server in audit_services.servers]
    caplog.set_level(logging.INFO, logger="mini_mpc.security")
    with pytest.raises(RuntimeError, match=message):
        main(cli_args(role, session_id, certificate=certificate))
    assert capsys.readouterr().out == ""
    assert [server.party.store.keys() for server in audit_services.servers] == keys
    events = [json.loads(record.message) for record in caplog.records if record.name == "mini_mpc.security"]
    assert len(events) == 1
    assert (events[0]["caller_identity"], events[0]["decision"], events[0]["error_code"]) == (
        certificate, "denied", code)
    assert not any(secret in caplog.text for secret in (
        "private-db-password", "private-insured-person", "private-patient-link", "PRIVATE KEY"))
