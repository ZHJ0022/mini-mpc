from pathlib import Path

from mini_mpc.applications.domain import AuditTask as DomainAuditTask
from mini_mpc.applications.domain import InsurerInpatientRecord as DomainInsurerRecord
from mini_mpc.applications.insurance_audit import AuditTask as AuditTaskCompat
from mini_mpc.applications.insurance_audit import InsurerInpatientRecord as InsurerRecordCompat
from mini_mpc.applications.insurance_audit import audit_window_start as AuditWindowCompat
from mini_mpc.applications.policies import audit_window_start


def test_package_root_does_not_keep_legacy_wrapper_modules():
    # 根包只保留 __init__.py；具体实现必须放在分层子包中。
    # Keep only __init__.py at the package root; implementations live in subpackages.
    package_root = Path(__file__).resolve().parents[1] / "src" / "mini_mpc"
    root_modules = {
        path.name
        for path in package_root.glob("*.py")
        if path.name != "__init__.py"
    }

    assert root_modules == set()


def test_application_domain_and_policy_exports_stay_compatible():
    # 应用编排层仍可转发领域对象，避免业务层调用方受内部拆分影响。
    # The application orchestrator may re-export domain objects after internal splits.
    assert AuditTaskCompat is DomainAuditTask
    assert InsurerRecordCompat is DomainInsurerRecord
    assert AuditWindowCompat is audit_window_start
