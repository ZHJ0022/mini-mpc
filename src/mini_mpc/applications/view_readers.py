"""Role-scoped read-only MySQL view access for local data nodes."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class MySQLReaderConfig:
    """Connection settings owned by exactly one organization's data node.

    每个机构节点只注入本方只读账号；数据库 GRANT 是最终权限边界。
    Each organization injects only its own view-only credentials. Database
    grants enforce the final read boundary.
    """

    host: str
    port: int
    user: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.host or not self.user:
            raise ValueError("MySQL host and user must not be empty")
        if type(self.port) is not int:
            raise TypeError("MySQL port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("MySQL port must be between 1 and 65535")


@dataclass(frozen=True)
class InsurerViewReader:
    """Read only the insurer-provided active-inpatient view.

    医保节点只查询本方正在住院 view；身份证号不在该 view 中。
    The insurer node queries its own active-inpatient view, which excludes ID cards.
    """

    config: MySQLReaderConfig

    def read(self, inpatient_record_id: str, hospital_id: str) -> Mapping[str, object]:
        return _fetch_one(
            self.config,
            "mini_mpc_insurer_test",
            """SELECT * FROM v_insurer_active_inpatients
               WHERE inpatient_record_id = %s AND hospital_id = %s""",
            (inpatient_record_id, hospital_id),
        )


@dataclass(frozen=True)
class TelcoViewReader:
    """Read only the telecom-provided hourly-presence view.

    运营商节点本地查询已授权任务的十个小时标记；不向外提供事件明细。
    The telecom node queries ten local presence flags for an authorized task.
    """

    config: MySQLReaderConfig

    def read(
        self, patient_link_key: str, hospital_id: str, audit_date: date
    ) -> Mapping[str, object]:
        return _fetch_one(
            self.config,
            "mini_mpc_telco_test",
            """SELECT * FROM v_telco_hourly_presence
               WHERE patient_link_key = %s AND hospital_id = %s AND audit_date = %s""",
            (patient_link_key, hospital_id, audit_date),
        )


@dataclass(frozen=True)
class HospitalViewReader:
    """Read only the hospital-provided clinical-scoring view.

    医院节点本地查询临床评分 view；诊断字段只在医院进程内使用。
    The hospital node reads its scoring view; clinical fields remain local.
    """

    config: MySQLReaderConfig

    def read(self, patient_link_key: str, hospital_id: str) -> Mapping[str, object]:
        return _fetch_one(
            self.config,
            "mini_mpc_hospital_test",
            """SELECT * FROM v_hospital_clinical_scoring
               WHERE patient_link_key = %s AND hospital_id = %s""",
            (patient_link_key, hospital_id),
        )


def _fetch_one(
    config: MySQLReaderConfig,
    database: str,
    query: str,
    params: tuple[object, ...],
) -> Mapping[str, object]:
    """Fetch one view row without exposing credentials or source fields in errors.

    仅在数据节点内建立连接；查询参数绑定由 PyMySQL 处理。
    Connect inside the data node and let PyMySQL bind query parameters.
    """

    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("install the optional mysql dependency") from exc

    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError("no matching view row for the audit task")
    return dict(row)
