import os
import json
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from mini_mpc.applications.insurance_audit import (
    compute_insurance_audit_result,
    create_audit_task_from_record,
    select_audit_candidates,
)
from mini_mpc.applications.nodes import HospitalNode, InsurerNode, TelcoNode
from mini_mpc.applications.preprocessing import (
    derive_hospital_clinical_need_score_from_view_row,
    derive_telco_features_from_hourly_view,
)
from mini_mpc.applications.view_adapters import (
    insurer_record_from_view_row,
)
from mini_mpc.applications.view_readers import (
    HospitalViewReader,
    InsurerViewReader,
    MySQLReaderConfig,
    TelcoViewReader,
)
from mini_mpc.runtime.backend import InMemoryShamirBackend
from mini_mpc.runtime.remote_backend import RemoteShamirBackend
from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.network.tls import MPCPartyTLSConfig
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.sharing.shamir import share_secret
from network_test_support import (
    write_test_pki,
)


MYSQL_SCHEMAS = {
    "insurer": "mini_mpc_insurer_test",
    "telco": "mini_mpc_telco_test",
    "hospital": "mini_mpc_hospital_test",
}

MYSQL_READER_DEFAULTS = {
    "insurer": ("mini_mpc_insurer_reader", "mini_mpc_insurer_reader"),
    "telco": ("mini_mpc_telco_reader", "mini_mpc_telco_reader"),
    "hospital": ("mini_mpc_hospital_reader", "mini_mpc_hospital_reader"),
}


@pytest.mark.parametrize(
    (
        "inpatient_record_id",
        "patient_link_key",
        "hospital_id",
        "expected_risk_level",
    ),
    [
        ("insurer-inpatient-001", "patient-link-001", "hospital-001", "medium"),
        ("insurer-inpatient-002", "patient-link-002", "hospital-001", "none"),
        ("insurer-inpatient-003", "patient-link-003", "hospital-001", "high"),
        ("insurer-inpatient-007", "patient-link-007", "hospital-001", "low"),
    ],
)
def test_mysql_views_feed_mpc_audit_flow_with_role_scoped_readers(
    inpatient_record_id,
    patient_link_key,
    hospital_id,
    expected_risk_level,
):
    # 测试真实 MySQL views 是否能按任务参数驱动完整 MPC 流程；每方只用自己的 view-only 账号读取本方 view。
    # Test real MySQL views feeding the full MPC flow by task parameters; each party uses its own view-only reader account.
    connection_config = _mysql_connection_config()
    pymysql = pytest.importorskip("pymysql")

    insurer_row = InsurerViewReader(_view_reader_config(connection_config, "insurer")).read(
        inpatient_record_id, hospital_id
    )
    telco_row = TelcoViewReader(_view_reader_config(connection_config, "telco")).read(
        patient_link_key, hospital_id, date(2026, 8, 28)
    )
    hospital_row = HospitalViewReader(_view_reader_config(connection_config, "hospital")).read(
        patient_link_key, hospital_id
    )

    audit_date = _as_date(telco_row["audit_date"])
    assert telco_row["patient_link_key"] == patient_link_key
    assert telco_row["hospital_id"] == hospital_id
    assert hospital_row["patient_link_key"] == patient_link_key
    assert hospital_row["hospital_id"] == hospital_id

    insurer_record = insurer_record_from_view_row(insurer_row)
    candidates = select_audit_candidates([insurer_record], audit_date)
    task = create_audit_task_from_record(
        candidates[0],
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key=hospital_row["patient_link_key"],
        audit_date=audit_date,
    )

    telco_features = derive_telco_features_from_hourly_view(telco_row)
    clinical_need_score = derive_hospital_clinical_need_score_from_view_row(
        hospital_row
    )

    backend = InMemoryShamirBackend()
    # 内存 backend 作为真实 view 输入的数学对照。
    # The in-memory backend provides a mathematical reference for real view inputs.
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
    assert result.risk_level == expected_risk_level
    assert not hasattr(result, "risk_score")


def test_mysql_role_nodes_submit_to_https_parties_and_insurer_receives_level(audit_services):
    # 三方分别读取本方 view，再直接向 HTTPS party 提交；医保只重构等级。
    # Role nodes read their own views and submit directly to HTTPS parties;
    # the insurer reconstructs only the level.
    connection_config = _mysql_connection_config()
    pytest.importorskip("pymysql")
    insurer_reader = InsurerViewReader(_view_reader_config(connection_config, "insurer"))
    telco_reader = TelcoViewReader(_view_reader_config(connection_config, "telco"))
    hospital_reader = HospitalViewReader(_view_reader_config(connection_config, "hospital"))

    cases = (
        ("001", "medium"),
        ("002", "none"),
        ("003", "high"),
        ("007", "low"),
    )
    party_clients = audit_services.role_clients["insurer"]
    backend = RemoteShamirBackend(party_clients)
    for case_id, expected_level in cases:
        patient_link_key = f"patient-link-{case_id}"
        session_id = f"audit-remote-{case_id}"
        audit_services.register(session_id)
        TelcoNode().submit_from_view(
            telco_reader, audit_services.sharing("telco", session_id),
            patient_link_key=patient_link_key,
            hospital_id="hospital-001",
            audit_date=date(2026, 8, 28),
        )
        HospitalNode().submit_from_view(
            hospital_reader, audit_services.sharing("hospital", session_id),
            patient_link_key=patient_link_key,
            hospital_id="hospital-001",
        )
        task = InsurerNode().audit_task_from_database(
            insurer_reader,
            inpatient_record_id=f"insurer-inpatient-{case_id}",
            hospital_id="hospital-001",
            audit_id=session_id,
            audit_case_id=f"audit-case-{case_id}",
            patient_link_key=patient_link_key,
            audit_date=date(2026, 8, 28),
        )
        result = compute_insurance_audit_result(task, backend=backend)
        assert result.risk_level == expected_level
        assert not hasattr(result, "risk_score")


def test_independent_party_and_role_processes_complete_mysql_audit(tmp_path):
    # 验证文档中的真实进程边界：三个 party 进程和三个机构命令进程。
    # Verify the documented process boundary with three party processes and
    # three separate organization command processes.
    connection_config = _mysql_connection_config()
    pytest.importorskip("pymysql")

    identities = (
        "insurer", "telco", "hospital", "party-1", "party-2", "party-3"
    )
    ca, materials, fingerprints = write_test_pki(tmp_path / "certs", identities)
    ports = []
    for _ in range(3):
        with socket.socket() as temporary_socket:
            temporary_socket.bind(("127.0.0.1", 0))
            ports.append(temporary_socket.getsockname()[1])

    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    processes = []
    event_paths = []
    try:
        for party_id, port in enumerate(ports, start=1):
            config_path = tmp_path / f"party{party_id}.json"
            party_cert, party_key = materials[f"party-{party_id}"]
            config_path.write_text(json.dumps({
                "party_id": party_id,
                "host": "127.0.0.1",
                "port": port,
                "tls": {
                    "certfile": party_cert,
                    "keyfile": party_key,
                    "cafile": ca,
                    "require_client_cert": True,
                },
                "client_fingerprints": fingerprints,
                # 目的地址由各 party 的配置固定，不能由医保请求改写。
                # Each party pins destinations in config; insurer requests cannot redirect them.
                "peer_endpoints": [
                    {"party_id": peer_id, "host": "127.0.0.1", "port": peer_port}
                    for peer_id, peer_port in enumerate(ports, start=1)
                ],
            }), encoding="utf-8")
            # 事件写入临时文件，既验证独立进程日志，也避免未消费的 PIPE 阻塞服务。
            # Capture events in a temporary file, avoiding an undrained PIPE blocking the service.
            event_path = tmp_path / f"party{party_id}.events.jsonl"
            event_paths.append(event_path)
            with event_path.open("w", encoding="utf-8") as event_stream:
                processes.append(subprocess.Popen(
                    [sys.executable, "-m", "mini_mpc.network.party_service", "--config", str(config_path)],
                    env=base_env, stdout=subprocess.DEVNULL, stderr=event_stream,
                ))

        insurer_cert, insurer_key = materials["insurer"]
        tls = MPCPartyTLSConfig(
            cafile=ca, certfile=insurer_cert, keyfile=insurer_key
        )
        for party_id, port in enumerate(ports, start=1):
            client = MPCPartyHttpClient(MPCPartyEndpoint(party_id, "127.0.0.1", port), tls_config=tls)
            deadline = time.monotonic() + 15
            while True:
                try:
                    assert client.health()["status"] == "ok"
                    break
                except (OSError, AssertionError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)

        common_args = [
            "--session-id", "audit-process-001",
            "--patient-link-key", "patient-link-001",
            "--hospital-id", "hospital-001",
            "--audit-date", "2026-08-28",
        ]
        # 登记和医保计算跨进程重试；输入方不重新随机分享已有特征。
        # Retry registration and insurer computation across processes, without resharing inputs.
        expires_at = int(time.time()) + 3600
        for role in ("insurer-register", "insurer-register", "telco", "hospital", "insurer", "insurer"):
            # 各命令进程只加载本机构客户端证书，身份不能由 CLI 参数伪造。
            # Each process loads its own client certificate; CLI arguments do not set identity.
            identity = "insurer" if role == "insurer-register" else role
            role_cert, role_key = materials[identity]
            client_config = tmp_path / f"remote_{role}.json"
            client_config.write_text(json.dumps({
                "threshold": 2,
                "num_parties": 3,
                "modulus": 251,
                "endpoints": [
                    {"party_id": party_id, "host": "127.0.0.1", "port": port}
                    for party_id, port in enumerate(ports, start=1)
                ],
                "tls": {
                    "cafile": ca, "certfile": role_cert, "keyfile": role_key,
                },
            }), encoding="utf-8")
            role_config = _role_connection_config(connection_config, identity)
            role_env = dict(base_env)
            for other_role in ("telco", "hospital", "insurer"):
                role_env.pop(f"MINI_MPC_{other_role.upper()}_MYSQL_USER", None)
                role_env.pop(f"MINI_MPC_{other_role.upper()}_MYSQL_PASSWORD", None)
            prefix = f"MINI_MPC_{identity.upper()}_MYSQL_"
            role_env[prefix + "HOST"] = role_config["host"]
            role_env[prefix + "PORT"] = str(role_config["port"])
            role_env[prefix + "USER"] = role_config["user"]
            role_env[prefix + "PASSWORD"] = role_config["password"]
            if identity == "insurer":
                insurer_env = role_env
            extra_args = (
                ["--inpatient-record-id", "insurer-inpatient-001",
                 "--audit-case-id", "audit-case-process-001"]
                if identity == "insurer" else []
            )
            if role == "insurer-register":
                extra_args += ["--expires-at", str(expires_at)]
            completed = subprocess.run(
                [sys.executable, "-m", "mini_mpc.applications.audit_cli", role,
                 "--client-config", str(client_config), *common_args, *extra_args],
                env=role_env, capture_output=True, text=True, timeout=180, check=True,
            )
            assert completed.stderr == ""
            assert completed.stdout.strip() == (
                "risk_level=medium" if role == "insurer" else (
                    "status=registered" if role == "insurer-register" else "status=submitted"
                )
            )
        def insurer_command(role, session_id, *, certificate="insurer", expiry=None):
            args = list(common_args)
            args[1] = session_id
            extra = ["--inpatient-record-id", "insurer-inpatient-001",
                     "--audit-case-id", "audit-case-process-001"]
            if expiry is not None:
                extra += ["--expires-at", str(expiry)]
            return subprocess.run(
                [sys.executable, "-m", "mini_mpc.applications.audit_cli", role,
                 "--client-config", str(tmp_path / f"remote_{certificate}.json"), *args, *extra],
                env=insurer_env, capture_output=True, text=True, timeout=30,
            )

        # CLI 子命令不能覆盖证书身份；已登记但缺输入的任务也不能产生等级。
        # CLI roles cannot override certificate identity; registered incomplete tasks produce no level.
        spoofed = insurer_command("insurer", "audit-process-001", certificate="telco")
        assert spoofed.returncode != 0 and spoofed.stdout == ""
        assert "not permitted" in spoofed.stderr
        assert insurer_command("insurer-register", "process-pending", expiry=expires_at).returncode == 0
        pending = insurer_command("insurer", "process-pending")
        assert pending.returncode != 0 and pending.stdout == ""
        assert "missing" in pending.stderr
        expiry = int(time.time()) + 3
        assert insurer_command("insurer-register", "process-expired", expiry=expiry).returncode == 0
        time.sleep(max(0, expiry - time.time()) + 0.05)
        expired = insurer_command("insurer", "process-expired")
        assert expired.returncode != 0 and expired.stdout == ""
        assert "expired" in expired.stderr
        for failed in (spoofed, pending, expired):
            assert insurer_env["MINI_MPC_INSURER_MYSQL_PASSWORD"] not in failed.stderr
            assert "PRIVATE KEY" not in failed.stderr
        # 每个服务都应记录完整白名单元数据和重复计算回执，不能记录份额值。
        # Every service records allowlisted metadata and retries, never share values.
        expected_keys = {"timestamp", "party_id", "session_id", "caller_identity",
                         "operation", "decision", "error_code"}
        denied_codes = set()
        for event_path in event_paths:
            events = [json.loads(line) for line in event_path.read_text().splitlines()]
            assert events and all(set(event) == expected_keys for event in events)
            assert any(event["decision"] == "retry" for event in events)
            assert any(event["operation"] == "read_result" for event in events)
            denied_codes.update(event["error_code"] for event in events if event["decision"] == "denied")
        assert {"permission_denied", "missing_input", "task_expired"} <= denied_codes
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("role", ("insurer", "telco", "hospital"))
def test_mysql_reader_accounts_are_limited_to_their_own_views(role):
    # 分别验证本方 view 可读、其他机构 view 及所有原始表不可读。
    # Check each reader's own view, then deny other views and every raw table.
    base = _mysql_connection_config()
    pymysql = pytest.importorskip("pymysql")
    config = _role_connection_config(base, role)
    views = {
        "insurer": "v_insurer_active_inpatients",
        "telco": "v_telco_hourly_presence",
        "hospital": "v_hospital_clinical_scoring",
    }
    tables = {
        "insurer": ("insurer_inpatient_records",),
        "telco": ("telco_location_events", "telco_hospital_cell_map", "telco_audit_requests"),
        "hospital": ("hospital_inpatient_records", "hospital_clinical_score_rules"),
    }
    _fetch_one(pymysql, config, MYSQL_SCHEMAS[role],
               f"SELECT * FROM {views[role]} LIMIT 1", ())
    denied = [(owner, view) for owner, view in views.items() if owner != role]
    denied += [(owner, table) for owner, names in tables.items() for table in names]
    for owner, name in denied:
        with pytest.raises(pymysql.err.OperationalError) as error:
            _fetch_one(pymysql, config, MYSQL_SCHEMAS[owner],
                       f"SELECT * FROM {name} LIMIT 1", ())
        # 只认可权限错误，不能把表缺失或连接失败误算成授权验证通过。
        # Require permission errors, not a missing table or a failed connection.
        assert error.value.args[0] in (1044, 1142, 1143)


def _mysql_connection_config() -> dict[str, object]:
    """Read MySQL integration-test connection settings.

    读取 MySQL 集成测试连接配置。
    Read connection settings for MySQL integration tests.
    """

    host = os.environ.get("MINI_MPC_MYSQL_HOST")
    if not host:
        pytest.skip("set MINI_MPC_MYSQL_HOST to run MySQL integration tests")

    return {
        "host": host,
        "port": int(os.environ.get("MINI_MPC_MYSQL_PORT", "3306")),
        "charset": "utf8mb4",
        "cursorclass": None,
    }


def _role_connection_config(
    base_config: dict[str, object],
    role: str,
) -> dict[str, object]:
    """Return connection settings for one party's view-only reader.

    返回某一方 view-only 只读账号的连接配置。
    Return connection settings for one party's view-only reader.
    """

    default_user, default_password = MYSQL_READER_DEFAULTS[role]
    config = dict(base_config)
    env_prefix = f"MINI_MPC_{role.upper()}_MYSQL"
    config["user"] = os.environ.get(f"{env_prefix}_USER", default_user)
    config["password"] = os.environ.get(f"{env_prefix}_PASSWORD", default_password)
    return config


def _view_reader_config(base_config: dict[str, object], role: str) -> MySQLReaderConfig:
    config = _role_connection_config(base_config, role)
    return MySQLReaderConfig(
        host=config["host"], port=config["port"],
        user=config["user"], password=config["password"],
    )


def _fetch_one(
    pymysql,
    connection_config: dict[str, object],
    database: str,
    query: str,
    params: tuple[object, ...],
) -> dict[str, object]:
    """Fetch one row from a MySQL view as a plain dict.

    从 MySQL view 中读取一行并转成普通 dict。
    Fetch one row from a MySQL view and return it as a plain dict.
    """

    config = dict(connection_config)
    config["database"] = database
    config["cursorclass"] = pymysql.cursors.DictCursor
    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
    finally:
        connection.close()

    if row is None:
        raise AssertionError(f"expected one row from {database}")
    return dict(row)


def _as_date(value: object) -> date:
    """Convert a MySQL DATE-like value to date.

    将 MySQL DATE-like 值转换为 date。
    Convert a MySQL DATE-like value to date.
    """

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("audit_date must be a date-like value")
