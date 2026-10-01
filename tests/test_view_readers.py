import sys
from datetime import date
from types import SimpleNamespace

from mini_mpc.applications.view_readers import (
    HospitalViewReader,
    InsurerViewReader,
    MySQLReaderConfig,
    TelcoViewReader,
)


def test_role_readers_use_only_their_views_and_bound_parameters(monkeypatch):
    # 三种 reader 固定访问本方 schema/view，并绑定查询参数。
    # Each role reader uses only its own schema/view and binds query parameters.
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query, params):
            calls.append((query, params))

        def fetchone(self):
            return {"matched": True}

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(
        connect=connect, cursors=SimpleNamespace(DictCursor=object)
    ))
    config = MySQLReaderConfig("localhost", 3306, "reader", "secret")

    InsurerViewReader(config).read("record-1", "hospital-1")
    TelcoViewReader(config).read("link-1", "hospital-1", date(2026, 8, 28))
    HospitalViewReader(config).read("link-1", "hospital-1")

    assert [calls[index]["database"] for index in (0, 2, 4)] == [
        "mini_mpc_insurer_test", "mini_mpc_telco_test", "mini_mpc_hospital_test"
    ]
    assert "v_insurer_active_inpatients" in calls[1][0]
    assert calls[1][1] == ("record-1", "hospital-1")
    assert "v_telco_hourly_presence" in calls[3][0]
    assert calls[3][1] == ("link-1", "hospital-1", date(2026, 8, 28))
    assert "v_hospital_clinical_scoring" in calls[5][0]
    assert calls[5][1] == ("link-1", "hospital-1")
    assert "secret" not in repr(config)
