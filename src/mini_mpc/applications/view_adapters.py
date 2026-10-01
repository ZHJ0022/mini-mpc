from collections.abc import Mapping
from datetime import datetime

from mini_mpc.applications.domain import InsurerInpatientRecord


MYSQL_TRUE_VALUES = {1, True, "1", "true", "TRUE", "yes", "YES"}
MYSQL_FALSE_VALUES = {0, False, "0", "false", "FALSE", "no", "NO"}


def insurer_record_from_view_row(row: Mapping[str, object]) -> InsurerInpatientRecord:
    """Convert one insurer read-only view row into an internal record.

    将医保只读视图的一行转换为内部住院记录对象。
    Convert one insurer read-only view row into the internal inpatient-record
    object.

    该函数是视图字段和应用内部结构之间的边界；医保业务字段不直接进入 MPC。
    This function is the boundary between view fields and application objects;
    insurer business fields do not enter MPC directly.
    """

    return InsurerInpatientRecord(
        insured_person_id=_required_str(row, "insured_person_id"),
        inpatient_record_id=_required_str(row, "inpatient_record_id"),
        hospital_id=_required_str(row, "hospital_id"),
        admission_time=_required_datetime(row, "admission_time"),
        is_currently_inpatient=_required_bit(row, "is_currently_inpatient") == 1,
        inpatient_status=_required_str(row, "inpatient_status"),
    )


def hourly_presence_from_view_row(row: Mapping[str, object]) -> list[int]:
    """Read the normalized telecom hourly-presence fields from a view row.

    从运营商本地归一化视图中读取 10 个小时级在院字段。
    Read ten hourly presence fields from a telecom-local normalized view row.

    小时字段由本地 MySQL view 聚合，事件明细保留在运营商数据库。
    The local MySQL view aggregates hourly flags; event details stay local.
    """

    hourly_presence = []
    for hour_index in range(10):
        # 按固定顺序读取 presence_h00..presence_h09，确保生成的序列和 22:00-08:00 窗口逐小时对齐。
        # Read presence_h00..presence_h09 in fixed order so the sequence aligns hour by hour with the 22:00-08:00 window.
        field_name = f"presence_h{hour_index:02d}"
        hourly_presence.append(_required_bit(row, field_name))
    return hourly_presence


def clinical_score_input_from_view_row(row: Mapping[str, object]) -> tuple[str, str]:
    """Read hospital clinical fields used by the current scoring rule.

    从医院只读视图中读取当前临床评分规则需要的字段。
    Read from the hospital read-only view the fields required by the current
    clinical scoring rule.

    诊断编码可保留在视图中供后续标准化使用；当前最小实现只需要疾病类别和严重程度。
    The diagnosis code may stay in the view for later normalization; the current
    minimal implementation only needs disease category and severity.
    """

    return (
        _required_str(row, "disease_category"),
        _required_str(row, "severity_level"),
    )


def clinical_need_score_from_view_row(row: Mapping[str, object]) -> int:
    """Read a hospital-derived clinical score from a view row.

    从医院 view 行读取医院本地派生出的临床必要性不足风险分。
    Read the hospital-derived clinical-necessity risk score from a view row.
    """

    value = _required_field(row, "clinical_need_score")
    if type(value) is not int:
        raise TypeError("clinical_need_score must be an integer")
    if not 0 <= value <= 10:
        raise ValueError("clinical_need_score must be between 0 and 10")
    return value


def _required_str(row: Mapping[str, object], field_name: str) -> str:
    """Read a required non-empty string from a MySQL view row.

    从MySQL 视图行读取必需的非空字符串字段。
    Read a required non-empty string field from a MySQL view row.
    """

    value = _required_field(row, field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _required_datetime(row: Mapping[str, object], field_name: str) -> datetime:
    """Read a required datetime value from a MySQL view row.

    从MySQL 视图行读取必需的 datetime 字段。
    Read a required datetime field from a MySQL view row.
    """

    value = _required_field(row, field_name)
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    return value


def _required_bit(row: Mapping[str, object], field_name: str) -> int:
    """Read a required MySQL-like boolean field as 0 or 1.

    将MySQL 视图中的布尔或 bit 字段读取为 0/1。
    Read a MySQL-like boolean or bit field from a simulated view row as 0 or 1.
    """

    value = _required_field(row, field_name)
    if type(value) not in (int, bool, str):
        raise TypeError(f"{field_name} must be a MySQL-like bit value")
    if value in MYSQL_TRUE_VALUES:
        return 1
    if value in MYSQL_FALSE_VALUES:
        return 0
    raise ValueError(f"{field_name} must be a MySQL-like bit value")


def _required_field(row: Mapping[str, object], field_name: str) -> object:
    """Return one required field from a MySQL view row.

    从MySQL 视图行返回一个必需字段。
    """

    if field_name not in row:
        raise KeyError(f"missing required field: {field_name}")
    return row[field_name]
