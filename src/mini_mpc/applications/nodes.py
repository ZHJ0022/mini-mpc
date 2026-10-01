from collections.abc import Mapping
from datetime import date

from mini_mpc.applications.domain import AuditTask, InsurerInpatientRecord
from mini_mpc.applications.features import validate_feature_value
from mini_mpc.applications.insurance_audit import (
    RISK_LEVEL_CODES,
    create_audit_task_from_record,
)
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
    TelcoViewReader,
)
from mini_mpc.runtime.remote_protocol import (
    SharingConfig,
    remote_classify_level_shares as _remote_classify_level_shares,
    submit_feature_shares,
)
from mini_mpc.sharing.share import Share


class InsurerNode:
    """Insurer-side node behavior.

    医保节点负责读取本方 view 行、执行本地筛选并创建稽核任务。
    Insurer-side node behavior: read its own view row, run local filtering, and
    create an audit task.
    """

    def audit_task_from_view_row(
        self,
        row: Mapping[str, object],
        *,
        audit_id: str,
        audit_case_id: str,
        patient_link_key: str,
        audit_date: date,
    ) -> AuditTask:
        """Create an audit task from an insurer-owned view row.

        从医保方 view 行创建稽核任务；原始医保字段不进入 MPC。
        Create an audit task from an insurer-owned view row; raw insurer fields
        do not enter MPC.
        """

        record = insurer_record_from_view_row(row)
        return self.audit_task_from_record(
            record,
            audit_id=audit_id,
            audit_case_id=audit_case_id,
            patient_link_key=patient_link_key,
            audit_date=audit_date,
        )

    def audit_task_from_database(
        self,
        reader: InsurerViewReader,
        *,
        inpatient_record_id: str,
        hospital_id: str,
        audit_id: str,
        audit_case_id: str,
        patient_link_key: str,
        audit_date: date,
    ) -> AuditTask:
        """Read the insurer's own view before creating an audit task.

        医保节点用本方 reader 取数并创建任务；原始行不交给 MPC。
        The insurer node reads its own view and keeps the source row out of MPC.
        """

        return self.audit_task_from_view_row(
            reader.read(inpatient_record_id, hospital_id),
            audit_id=audit_id,
            audit_case_id=audit_case_id,
            patient_link_key=patient_link_key,
            audit_date=audit_date,
        )

    def audit_task_from_record(
        self,
        record: InsurerInpatientRecord,
        *,
        audit_id: str,
        audit_case_id: str,
        patient_link_key: str,
        audit_date: date,
    ) -> AuditTask:
        """Create an audit task from an already-local insurer record.

        从医保本地记录创建任务；任务编号来自任务管理侧，不来自住院记录号。
        Create an audit task from a local insurer record; task ids come from
        task management, not from the inpatient record id.
        """

        return create_audit_task_from_record(
            record,
            audit_id=audit_id,
            audit_case_id=audit_case_id,
            patient_link_key=patient_link_key,
            audit_date=audit_date,
        )


class TelcoNode:
    """Telecom node behavior.

    运营商节点读取本方 view 行，派生缺席特征，并把 shares 发给 MPC parties。
    Telecom node behavior: read its own view row, derive absence features, and
    send shares to MPC parties.
    """

    def submit_feature_shares(
        self,
        row: Mapping[str, object],
        sharing: SharingConfig,
    ) -> None:
        """Derive telecom features and submit their shares.

        派生运营商特征并提交 shares；原始位置字段不会离开运营商侧。
        Derive telecom features and submit their shares; raw location fields do
        not leave the telecom side.
        """

        features = derive_telco_features_from_hourly_view(row)
        validate_feature_value("telco_absence_hours", features.telco_absence_hours)
        validate_feature_value("telco_max_absence_streak", features.telco_max_absence_streak)
        # 完整 shares 集合只在运营商节点的本地调用栈中出现，不作为响应返回。
        # Keep the full share set in the telecom call stack; do not return it.
        submit_feature_shares("telco_absence_hours", features.telco_absence_hours, sharing)
        submit_feature_shares("telco_max_absence_streak", features.telco_max_absence_streak, sharing)

    def submit_from_view(
        self,
        reader: TelcoViewReader,
        sharing: SharingConfig,
        *,
        patient_link_key: str,
        hospital_id: str,
        audit_date: date,
    ) -> None:
        """Read the telecom view locally, then submit feature shares directly.

        查询和明文特征提取均发生在运营商节点内；外部只看到提交状态。
        View access and plaintext derivation stay inside the telecom node.
        """

        self.submit_feature_shares(
            reader.read(patient_link_key, hospital_id, audit_date), sharing
        )


class HospitalNode:
    """Hospital node behavior.

    医院节点读取本方 view 行，并把医院本地派生的临床分数分享给 MPC parties。
    Hospital node behavior: read its own view row and share the hospital-derived
    clinical score with MPC parties.
    """

    def submit_feature_shares(
        self,
        row: Mapping[str, object],
        sharing: SharingConfig,
    ) -> None:
        """Submit shares of the hospital-derived clinical score.

        提交医院本地临床分数的 shares；诊断和严重程度字段不发送给 MPC party。
        Submit shares of the hospital-derived clinical score; diagnosis and
        severity fields are not sent to MPC parties.
        """

        clinical_need_score = derive_hospital_clinical_need_score_from_view_row(row)
        validate_feature_value("clinical_need_score", clinical_need_score)
        # 完整 shares 集合不通过医院节点接口返回。
        # Do not return the complete share set from the hospital node.
        submit_feature_shares("clinical_need_score", clinical_need_score, sharing)

    def submit_from_view(
        self,
        reader: HospitalViewReader,
        sharing: SharingConfig,
        *,
        patient_link_key: str,
        hospital_id: str,
    ) -> None:
        """Read the hospital view locally and submit clinical-score shares.

        医院字段和明文临床分数保留在医院节点的本地调用栈中。
        Clinical fields and the plaintext score stay in the hospital node.
        """

        self.submit_feature_shares(reader.read(patient_link_key, hospital_id), sharing)


def remote_classify_level_shares(
    sharing: SharingConfig,
    score_secret_id: str,
    *,
    thresholds: Mapping[str, int],
    score_range: tuple[int, int],
    default_level: str = "none",
) -> list[Share]:
    """Classify remote score shares with the insurance-audit level codes.

    使用医保稽核应用的等级编码分类远程 score shares。
    Classify remote score shares with the insurance-audit application's level
    codes.
    """

    # 通用远程协议要求显式传 level_codes；节点层保留应用默认值，避免测试和调用方重复配置。
    # The generic remote protocol requires explicit level_codes; the node layer keeps the application default to avoid repeated caller config.
    return _remote_classify_level_shares(
        sharing,
        score_secret_id,
        thresholds=thresholds,
        level_codes=RISK_LEVEL_CODES,
        score_range=score_range,
        default_level=default_level,
    )
