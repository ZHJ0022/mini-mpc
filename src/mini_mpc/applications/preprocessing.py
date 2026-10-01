from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from mini_mpc.applications.domain import InsurerInpatientRecord
from mini_mpc.applications.features import (
    TelcoPresenceFeatures,
    derive_clinical_need_score,
    derive_telco_presence_features,
)
from mini_mpc.applications.policies import (
    AUDIT_WINDOW_HOURS,
    AUDIT_WINDOW_START_HOUR,
    audit_window_start,
)
from mini_mpc.applications.view_adapters import (
    clinical_need_score_from_view_row,
    clinical_score_input_from_view_row,
    hourly_presence_from_view_row,
)


DEFAULT_CLINICAL_SCORE_TABLE = {
    ("respiratory", "moderate"): 6,
    ("orthopedic", "mild"): 2,
    ("administrative", "mild"): 10,
    ("cardiac", "severe"): 0,
}


def is_eligible_for_audit(
    record: InsurerInpatientRecord,
    audit_date: date,
) -> bool:
    """Check whether an insurer-side record can start an audit task.

    医保方本地判断住院记录是否适合抽查；当前只选择仍在住院且入院时间不晚于抽查窗口开始的记录。
    Locally check whether an insurer-side inpatient record is eligible for
    audit; the current rule keeps only currently inpatient records admitted no
    later than the audit-window start.
    """

    # 先检查是否仍在住院，避免已结束住院的记录进入 MPC 任务创建流程。
    # Check current-stay status first so completed stays do not enter audit-task creation.
    if not record.is_currently_inpatient:
        return False

    # 使用入院时间的时区信息构造同一时区下的窗口，避免 aware/naive datetime 混合比较。
    # Build the window in the admission timestamp's timezone to avoid mixing aware and naive datetimes.
    window_start = audit_window_start(audit_date, tzinfo=record.admission_time.tzinfo)
    return record.admission_time <= window_start


def select_audit_candidates(
    records: Sequence[InsurerInpatientRecord],
    audit_date: date,
) -> list[InsurerInpatientRecord]:
    """Select insurer-side inpatient records eligible for MPC audit.

    从医保本地住院记录中筛选可创建 MPC 稽核任务的候选患者。
    Select from insurer-side inpatient records the patients eligible for MPC
    audit-task creation.
    """

    candidates = []
    for record in records:
        # 逐条检查医保本地记录；只有通过本方规则的记录才进入跨方 MPC 任务。
        # Check each insurer-local record; only locally eligible records enter the cross-party MPC task.
        if is_eligible_for_audit(record, audit_date):
            candidates.append(record)
    return candidates


@dataclass(frozen=True)
class TelcoLocationEvent:
    """Normalized telecom location event used only inside the telecom node.

    运营商节点内部使用的标准化位置事件；事件明细不进入 MPC。
    Normalized telecom location event used inside the telecom node; event
    details do not enter MPC.
    """

    patient_link_key: str
    location_timestamp: datetime
    nCGI: str
    TAI: str | None = None
    subscriber_id: str | None = None

    def __post_init__(self) -> None:
        """Validate event fields before local geofencing.

        本地围栏匹配前校验事件标识及时间类型。
        Check event identifiers and timestamp type before local geofencing.
        """

        if not self.patient_link_key:
            raise ValueError("patient_link_key must not be empty")
        if not isinstance(self.location_timestamp, datetime):
            raise TypeError("location_timestamp must be a datetime")
        if not self.nCGI:
            raise ValueError("nCGI must not be empty")
        if self.TAI is not None and not self.TAI:
            raise ValueError("TAI must be non-empty when provided")
        if self.subscriber_id is not None and not self.subscriber_id:
            raise ValueError("subscriber_id must be non-empty when provided")


@dataclass(frozen=True)
class HospitalCellMapping:
    """Hospital coverage mapping maintained by the telecom side.

    运营商侧维护的医院覆盖区映射；用于本地判断事件是否落在目标医院覆盖范围。
    Hospital coverage mapping maintained by the telecom side; used locally to
    decide whether an event matches the target hospital area.
    """

    hospital_id: str
    nCGI: str
    TAI: str | None
    valid_from: datetime
    valid_to: datetime | None = None
    mapping_version: str | None = None

    def __post_init__(self) -> None:
        """Validate mapping fields before matching events.

        匹配事件前校验映射字段；时间有效期用于避免使用过期小区关系。
        Validate mapping fields before matching events; validity windows avoid
        using expired cell relations.
        """

        if not self.hospital_id:
            raise ValueError("hospital_id must not be empty")
        if not self.nCGI:
            raise ValueError("nCGI must not be empty")
        if self.TAI is not None and not self.TAI:
            raise ValueError("TAI must be non-empty when provided")
        if not isinstance(self.valid_from, datetime):
            raise TypeError("valid_from must be a datetime")
        if self.valid_to is not None and not isinstance(self.valid_to, datetime):
            raise TypeError("valid_to must be a datetime when provided")
        if self.valid_to is not None:
            _require_same_time_mode(self.valid_to, self.valid_from)
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be after valid_from")
        if self.mapping_version is not None and not self.mapping_version:
            raise ValueError("mapping_version must be non-empty when provided")


def derive_telco_features_from_hourly_view(
    row: Mapping[str, object],
) -> TelcoPresenceFeatures:
    """Derive telecom features from a normalized hourly-presence view row.

    从已归一化的运营商小时级 view 行派生 MPC 特征。
    Derive MPC features from a normalized telecom hourly-presence view row.

    该路径复用现有 view-only MySQL 测试，表示运营商已经在本地数据库中完成预聚合。
    This path reuses the current view-only MySQL tests and represents telecom
    preprocessing already materialized inside the provider's database.
    """

    return derive_telco_presence_features(hourly_presence_from_view_row(row))


def derive_telco_hourly_presence_from_events(
    events: Sequence[TelcoLocationEvent],
    mappings: Sequence[HospitalCellMapping],
    *,
    patient_link_key: str,
    hospital_id: str,
    audit_date: date,
    audit_timezone: tzinfo | None = None,
) -> list[int]:
    """Build the ten-hour presence sequence from local telecom events.

    从运营商本地事件和医院覆盖区映射生成 10 小时在院序列。
    Build the ten-hour presence sequence from telecom-local events and hospital
    coverage mappings.

    只有匹配目标患者、目标医院、审计窗口和有效映射的事件会被计为在院。
    Only events matching the target patient, hospital, audit window, and a
    valid mapping are counted as present.
    """

    _validate_target(patient_link_key, "patient_link_key")
    _validate_target(hospital_id, "hospital_id")
    # 无时区输入沿用 MySQL 本地时间；带时区输入须指定审计时区。
    # Naive inputs retain MySQL local time; aware inputs require an audit timezone.
    window_start = audit_window_start(audit_date, tzinfo=audit_timezone)
    for event in events:
        _require_same_time_mode(event.location_timestamp, window_start)
    for mapping in mappings:
        _require_same_time_mode(mapping.valid_from, window_start)
        if mapping.valid_to is not None:
            _require_same_time_mode(mapping.valid_to, window_start)
    window_end = window_start + timedelta(hours=AUDIT_WINDOW_HOURS)
    hourly_presence = [0] * AUDIT_WINDOW_HOURS

    for event in events:
        if event.patient_link_key != patient_link_key:
            continue
        if not window_start <= event.location_timestamp < window_end:
            continue

        # 仅使用事件发生时有效、且 nCGI/TAI 匹配的医院覆盖映射。
        # Use mappings valid at the event time with matching nCGI/TAI.
        if not any(_mapping_matches_event(mapping, event, hospital_id) for mapping in mappings):
            continue

        hour_index = int(
            (event.location_timestamp - window_start).total_seconds() // 3600
        )
        hourly_presence[hour_index] = 1

    return hourly_presence


def derive_telco_features_from_events(
    events: Sequence[TelcoLocationEvent],
    mappings: Sequence[HospitalCellMapping],
    *,
    patient_link_key: str,
    hospital_id: str,
    audit_date: date,
    audit_timezone: tzinfo | None = None,
) -> TelcoPresenceFeatures:
    """Derive telecom MPC features from normalized local events.

    从标准化运营商本地事件派生 MPC 特征。
    Derive telecom MPC features from normalized telecom-local events.

    该函数只输出小整数特征；原始时间戳、nCGI 和 TAI 不离开运营商节点。
    This function outputs only small integer features; raw timestamps, nCGI,
    and TAI stay inside the telecom node.
    """

    hourly_presence = derive_telco_hourly_presence_from_events(
        events,
        mappings,
        patient_link_key=patient_link_key,
        hospital_id=hospital_id,
        audit_date=audit_date,
        audit_timezone=audit_timezone,
    )
    return derive_telco_presence_features(hourly_presence)


def derive_hospital_clinical_need_score_from_view_row(
    row: Mapping[str, object],
    *,
    score_table: Mapping[tuple[str, str], int] = DEFAULT_CLINICAL_SCORE_TABLE,
) -> int:
    """Derive the hospital clinical score from a read-only view row.

    从医院只读 view 行派生临床必要性不足风险分。
    Derive the clinical-necessity risk score from a hospital read-only view row.

    若 view 已提供 `clinical_need_score`，直接读取该本地派生结果；否则用疾病类别和严重程度在本地规则表中计算。
    If the view already exposes `clinical_need_score`, read that local derived
    result; otherwise compute it from disease category and severity locally.
    """

    if "clinical_need_score" in row:
        return clinical_need_score_from_view_row(row)

    disease_category, severity_level = clinical_score_input_from_view_row(row)
    return derive_clinical_need_score(
        disease_category=disease_category,
        severity_level=severity_level,
        score_table=score_table,
    )


def telco_location_event_from_view_row(
    row: Mapping[str, object],
) -> TelcoLocationEvent:
    """Convert one telecom event view row into a normalized local event.

    将运营商事件 view 行转换为节点内部标准事件。
    Convert one telecom event view row into the node's normalized local event.
    """

    return TelcoLocationEvent(
        subscriber_id=_optional_str(row, "subscriber_id"),
        patient_link_key=_required_str(row, "patient_link_key"),
        location_timestamp=_required_datetime(row, "location_timestamp"),
        nCGI=_required_str(row, "nCGI"),
        TAI=_optional_str(row, "TAI"),
    )


def hospital_cell_mapping_from_view_row(
    row: Mapping[str, object],
) -> HospitalCellMapping:
    """Convert one mapping view row into a telecom-local coverage rule.

    将医院覆盖区映射 view 行转换为运营商本地匹配规则。
    """

    return HospitalCellMapping(
        hospital_id=_required_str(row, "hospital_id"),
        nCGI=_required_str(row, "nCGI"),
        TAI=_optional_str(row, "TAI"),
        mapping_version=_optional_str(row, "mapping_version"),
        valid_from=_required_datetime(row, "valid_from"),
        valid_to=_optional_datetime(row, "valid_to"),
    )


def _mapping_matches_event(
    mapping: HospitalCellMapping,
    event: TelcoLocationEvent,
    hospital_id: str,
) -> bool:
    if mapping.hospital_id != hospital_id:
        return False
    if mapping.nCGI != event.nCGI:
        return False
    if mapping.TAI is not None and mapping.TAI != event.TAI:
        return False
    if event.location_timestamp < mapping.valid_from:
        return False
    if mapping.valid_to is not None and event.location_timestamp >= mapping.valid_to:
        return False
    return True


def _validate_target(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")


def _required_str(row: Mapping[str, object], field_name: str) -> str:
    value = _required_field(row, field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _optional_str(row: Mapping[str, object], field_name: str) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string when provided")
    if not value:
        raise ValueError(f"{field_name} must be non-empty when provided")
    return value


def _required_datetime(row: Mapping[str, object], field_name: str) -> datetime:
    value = _required_field(row, field_name)
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    return value


def _optional_datetime(row: Mapping[str, object], field_name: str) -> datetime | None:
    value = row.get(field_name)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime when provided")
    return value


def _required_field(row: Mapping[str, object], field_name: str) -> object:
    if field_name not in row:
        raise KeyError(f"missing required field: {field_name}")
    return row[field_name]


def _require_same_time_mode(timestamp: datetime, reference: datetime) -> None:
    """Reject mixing timezone-aware and naive timestamps before comparisons.

    比较前拒绝带时区与无时区时间混用；不同 aware 时区按实际时刻比较。
    """
    if (timestamp.utcoffset() is None) != (reference.utcoffset() is None):
        raise ValueError("timestamps must match the audit window timezone awareness")
