from datetime import date, datetime

from mini_mpc.applications.features import (
    derive_clinical_need_score,
    derive_telco_presence_features,
)
from mini_mpc.applications.insurance_audit import (
    InsurerInpatientRecord,
    compute_insurance_audit_result,
    create_audit_task_from_record,
    select_audit_candidates,
)
from mini_mpc.runtime.backend import InMemoryShamirBackend
from mini_mpc.sharing.shamir import share_secret


def main() -> None:
    """Run the minimal insurance-audit MPC flow."""

    # 第 1 步：医保方先在本地准备抽查日期；audit_date 是任务运行参数，不是数据库字段。
    # Step 1: The insurer first prepares the audit date locally; audit_date is a runtime parameter, not a database field.
    audit_date = date(2026, 8, 28)

    # 第 2 步：医保方在本地读取最小住院记录，并只把符合条件的记录用于创建稽核任务。
    # Step 2: The insurer reads minimal inpatient records locally and uses only eligible records to create audit tasks.
    #
    # 这里的住院记录来自医保业务库，但不会直接进入 MPC；它只用于本地前置筛选和任务路由。
    # This inpatient record comes from the insurer's business database but does not enter MPC; it is used for local prefiltering and task routing only.
    insurer_record = InsurerInpatientRecord(
        insured_person_id="insured-001",
        inpatient_record_id="inpatient-record-001",
        hospital_id="hospital-001",
        admission_time=datetime(2026, 8, 28, 21, 30),
        is_currently_inpatient=True,
        inpatient_status="emergency",
    )
    candidates = select_audit_candidates([insurer_record], audit_date)
    if not candidates:
        raise RuntimeError("no eligible inpatient records for audit")

    # 第 3 步：任务管理侧创建 audit_id 和 audit_case_id，再结合医保本地记录生成 AuditTask。
    # Step 3: The task-management layer creates audit_id and audit_case_id, then combines them with the insurer-side record to create AuditTask.
    #
    # audit_case_id 是项目管理侧编号，不是医保业务库里的住院记录编号。
    # audit_case_id is a project-management identifier, not the inpatient record id from the insurer's business database.
    task = create_audit_task_from_record(
        candidates[0],
        audit_id="audit-001",
        audit_case_id="audit-case-001",
        patient_link_key="patient-link-001",
        audit_date=audit_date,
    )

    # 第 4 步：运营商在本地把标准化位置记录转换成 10 个小时的在院序列。
    # Step 4: The telecom provider locally converts normalized location records into 10 hourly presence flags.
    #
    # 这里直接使用已经预处理好的序列，示例不解析原始 nCGI、TAI 或时间戳。
    # This example uses an already preprocessed sequence and does not parse raw nCGI, TAI, or timestamps.
    hourly_presence = [1, 1, 0, 0, 0, 1, 1, 0, 0, 1]
    telco_features = derive_telco_presence_features(hourly_presence)

    # 第 5 步：医院在本地根据诊断类别和严重程度映射临床必要性不足风险分。
    # Step 5: The hospital locally maps diagnosis category and severity to a
    # risk score for weak clinical support for inpatient care.
    #
    # 分数表放在应用层，后续评估模型时可以调整，不需要改 MPC 协议。
    # The score table stays in the application layer so model evaluation can adjust it without changing MPC protocols.
    clinical_score_table = {
        ("respiratory", "mild"): 9,
        ("respiratory", "moderate"): 6,
        ("respiratory", "severe"): 3,
    }
    clinical_need_score = derive_clinical_need_score(
        disease_category="respiratory",
        severity_level="moderate",
        score_table=clinical_score_table,
    )

    # 第 6 步：模拟数据方各自生成 shares；正式远程路径由各方节点直接提交。
    # Step 6: Simulate data holders creating shares locally; remote nodes submit directly.
    backend = InMemoryShamirBackend()
    for name, value in (
        ("telco_absence_hours", telco_features.telco_absence_hours),
        ("telco_max_absence_streak", telco_features.telco_max_absence_streak),
        ("clinical_need_score", clinical_need_score),
    ):
        for share in share_secret(value, threshold=2, num_parties=3, modulus=251):
            backend.submit_feature_share(task.audit_id, name, share)

    # 第 7 步：业务入口只引用已经提交的特征 shares，并向医保披露等级。
    # Step 7: The business entry references submitted shares and discloses only level.
    result = compute_insurance_audit_result(
        task,
        threshold=2,
        num_parties=3,
        modulus=251,
        receiver_id="insurer",
        backend=backend,
    )

    # 第 8 步：只打印医保方最终获得的等级，不输出原始数据或特征。
    # Step 8: Print only the insurer-visible level, not source data or features.
    print(f"audit_id={result.audit_id}")
    print(f"audit_case_id={result.audit_case_id}")
    print(f"risk_level={result.risk_level}")


if __name__ == "__main__":
    main()
