from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class InsurerInpatientRecord:
    """Minimal insurer-side inpatient record used before MPC.

    医保方本地前置筛选使用的最小住院记录，不作为 MPC 输入。
    Minimal insurer-side inpatient record used for local prefiltering; it is not
    an MPC input.

    这些字段来自医保业务库，用于确定是否可以创建稽核任务，并保留就诊或入院类型供后续评估。
    These fields come from the insurer's business database, decide whether an
    audit task can be created, and keep the visit or admission type for later
    assessment.
    """

    insured_person_id: str
    inpatient_record_id: str
    hospital_id: str
    admission_time: datetime
    is_currently_inpatient: bool
    inpatient_status: str

    def __post_init__(self) -> None:
        """Validate insurer-side record fields before local filtering.

        在医保本地筛选前校验住院记录字段；预期标识字段非空且入院时间为 datetime。
        Validate insurer-side record fields before local filtering; expect
        non-empty identifiers and a datetime admission time.
        """

        if not self.insured_person_id:
            raise ValueError("insured_person_id must not be empty")
        if not self.inpatient_record_id:
            raise ValueError("inpatient_record_id must not be empty")
        if not self.hospital_id:
            raise ValueError("hospital_id must not be empty")
        if not isinstance(self.admission_time, datetime):
            raise TypeError("admission_time must be a datetime")
        if not isinstance(self.is_currently_inpatient, bool):
            raise TypeError("is_currently_inpatient must be a bool")
        if not self.inpatient_status:
            raise ValueError("inpatient_status must not be empty")


@dataclass(frozen=True)
class AuditTask:
    """Runtime task parameters supplied by the insurer.

    医保方发起一次 MPC 稽核任务时提供的运行参数。
    Runtime parameters supplied by the insurer when starting one MPC audit task.

    这些字段用于任务路由和数据对齐，不作为私有风险特征进入 MPC。
    These fields are used for task routing and record alignment; they are not
    private risk features entering MPC.
    """

    audit_id: str
    audit_case_id: str
    patient_link_key: str
    hospital_id: str
    audit_date: date

    def __post_init__(self) -> None:
        """Validate task routing fields before starting MPC.

        在进入 MPC 前校验任务路由字段；预期所有标识字段都非空。
        Validate task routing fields before MPC; expect all identifier fields to
        be non-empty.
        """

        if not self.audit_id:
            raise ValueError("audit_id must not be empty")
        if not self.audit_case_id:
            raise ValueError("audit_case_id must not be empty")
        if not self.patient_link_key:
            raise ValueError("patient_link_key must not be empty")
        if not self.hospital_id:
            raise ValueError("hospital_id must not be empty")
