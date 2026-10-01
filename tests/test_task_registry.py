"""Registration boundaries for party-local audit sessions."""

import pytest

from mini_mpc.network.task_registry import TaskExpiredError, TaskManifest, TaskRegistry


def _manifest(*, session_id: str = "audit-001", expires_at: int = 1200) -> TaskManifest:
    return TaskManifest(
        session_id=session_id,
        expires_at=expires_at,
        threshold=2,
        num_parties=3,
        modulus=251,
        model_version="insurance-audit-v1",
        feature_secret_ids=(
            "telco_absence_hours", "telco_max_absence_streak",
            "clinical_need_score",
        ),
    )


def test_registry_requires_live_session_and_accepts_identical_retry(monkeypatch):
    # 相同清单可在网络中断后重试；过期会话不能继续使用。
    # Identical registration retries are safe; expired sessions cannot be used.
    monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: 1000)
    registry = TaskRegistry(party_id=1)
    manifest = _manifest()
    with pytest.raises(KeyError, match="not registered"):
        registry.require(manifest.session_id)

    registry.register(manifest)
    registry.register(manifest)
    assert registry.require(manifest.session_id) == manifest
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_manifest(expires_at=1300))

    monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: 1200)
    with pytest.raises(TaskExpiredError, match="expired"):
        registry.require(manifest.session_id)
    # 过期任务的原清单和延长期限都不能恢复会话。
    # Neither the original manifest nor an extended deadline can revive the session.
    for retry in (manifest, _manifest(expires_at=1300)):
        with pytest.raises(TaskExpiredError, match="expired"):
            registry.register(retry)
    assert registry._tasks[manifest.session_id] == manifest


def test_manifest_rejects_invalid_protocol_and_raw_routing_fields(monkeypatch):
    monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: 1000)
    registry = TaskRegistry(party_id=1)
    with pytest.raises(ValueError, match="expiry"):
        registry.register(_manifest(expires_at=1000))
    with pytest.raises(ValueError, match="expiry"):
        registry.register(_manifest(expires_at=1000 + 24 * 60 * 60 + 1))

    payload = _manifest().to_payload()
    payload["patient_link_key"] = "patient-link-001"
    with pytest.raises(ValueError, match="unexpected"):
        TaskManifest.from_payload(payload)
    with pytest.raises(ValueError, match="modulus must be prime"):
        TaskManifest(**{**_manifest().__dict__, "modulus": 252})
