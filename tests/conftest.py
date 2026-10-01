import pytest

from network_test_support import RunningAuditClients


@pytest.fixture(scope="session")
def audit_services():
    # 共用正式服务，每个用例用独立任务隔离状态，避免反复签发证书。
    # Reuse deployed services with unique tasks to isolate state and avoid repeated PKI setup.
    with RunningAuditClients() as services:
        yield services
