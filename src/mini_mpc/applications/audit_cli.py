"""One-shot, role-scoped audit node entry for the local multi-process demo."""

from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Sequence

from mini_mpc.applications.insurance_audit import (
    AUDIT_MODEL_VERSION,
    DEFAULT_RISK_MODEL,
    compute_insurance_audit_result,
)
from mini_mpc.applications.nodes import HospitalNode, InsurerNode, TelcoNode
from mini_mpc.applications.view_readers import (
    HospitalViewReader,
    InsurerViewReader,
    MySQLReaderConfig,
    TelcoViewReader,
)
from mini_mpc.network.config import load_remote_client_config
from mini_mpc.runtime.remote_backend import RemoteShamirBackend
from mini_mpc.runtime.remote_protocol import SharingConfig, remote_register_task


def _reader_config(role: str) -> MySQLReaderConfig:
    """Load only the current role's database credentials from environment.

    各进程只读取本方数据库环境变量；密码不进入 party 配置或命令行参数。
    Each process reads only its own DB environment variables; passwords stay
    out of party config files and command-line arguments.
    """

    prefix = f"MINI_MPC_{role.upper()}_MYSQL_"
    return MySQLReaderConfig(
        host=os.environ.get(prefix + "HOST", "127.0.0.1"),
        port=int(os.environ.get(prefix + "PORT", "3306")),
        user=os.environ[prefix + "USER"],
        password=os.environ[prefix + "PASSWORD"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one data-holder submission or insurer result collection.

    医保先注册任务，数据方再提交，最后由医保计算并取等级。
    The insurer registers first, providers submit next, and the insurer collects the level.
    """

    parser = argparse.ArgumentParser(description="Run one role-scoped MPC audit step")
    subparsers = parser.add_subparsers(dest="role", required=True)
    for role in ("telco", "hospital", "insurer-register", "insurer"):
        sub = subparsers.add_parser(role)
        sub.add_argument("--client-config", required=True)
        sub.add_argument("--session-id", required=True)
        sub.add_argument("--patient-link-key", required=True)
        sub.add_argument("--hospital-id", required=True)
        sub.add_argument("--audit-date", required=True, type=date.fromisoformat)
        if role in ("insurer-register", "insurer"):
            sub.add_argument("--inpatient-record-id", required=True)
            sub.add_argument("--audit-case-id", required=True)
        if role == "insurer-register":
            # 显式截止时间使跨 party 重试发送的任务清单保持完全相同。
            # An explicit deadline keeps retries identical across party services.
            sub.add_argument("--expires-at", required=True, type=int)
    args = parser.parse_args(argv)

    config = load_remote_client_config(args.client_config)
    backend = RemoteShamirBackend.from_config(args.client_config)
    sharing = SharingConfig(
        session_id=args.session_id,
        threshold=config.threshold,
        num_parties=config.num_parties,
        modulus=config.modulus,
        party_clients=backend.party_clients,
    )

    if args.role == "telco":
        TelcoNode().submit_from_view(
            TelcoViewReader(_reader_config("telco")), sharing,
            patient_link_key=args.patient_link_key,
            hospital_id=args.hospital_id,
            audit_date=args.audit_date,
        )
        print("status=submitted")
        return 0

    if args.role == "hospital":
        HospitalNode().submit_from_view(
            HospitalViewReader(_reader_config("hospital")), sharing,
            patient_link_key=args.patient_link_key,
            hospital_id=args.hospital_id,
        )
        print("status=submitted")
        return 0

    task = InsurerNode().audit_task_from_database(
        InsurerViewReader(_reader_config("insurer")),
        inpatient_record_id=args.inpatient_record_id,
        hospital_id=args.hospital_id,
        audit_id=args.session_id,
        audit_case_id=args.audit_case_id,
        patient_link_key=args.patient_link_key,
        audit_date=args.audit_date,
    )
    if args.role == "insurer-register":
        remote_register_task(
            sharing,
            expires_at=args.expires_at,
            model_version=AUDIT_MODEL_VERSION,
            feature_secret_ids=DEFAULT_RISK_MODEL.feature_names,
        )
        print("status=registered")
        return 0

    result = compute_insurance_audit_result(
        task,
        threshold=config.threshold,
        num_parties=config.num_parties,
        modulus=config.modulus,
        backend=backend,
    )
    print(f"risk_level={result.risk_level}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
