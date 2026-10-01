"""Party-local registration of bounded audit tasks.

每个 party 独立保存任务清单，供后续 share 与计算请求校验。
Each party keeps its own manifest for later share and computation checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock
from time import time

from mini_mpc.math.field import is_prime
from mini_mpc.network.authorization import FEATURE_OWNERS


MAX_TASK_LIFETIME_SECONDS = 24 * 60 * 60


class TaskExpiredError(PermissionError):
    """Expired task; no cached success may bypass its lifetime.

    任务已过期，已完成操作的缓存回执也不能绕过有效期。
    The task expired; cached acknowledgements cannot bypass its lifetime.
    """


@dataclass(frozen=True)
class TaskManifest:
    """Public protocol limits for one audit session; no patient identifiers.

    一次稽核的公开协议边界，不保存患者联结键或原始业务字段。
    Public protocol limits for one audit; patient and source fields stay outside.
    """

    session_id: str
    expires_at: int
    threshold: int
    num_parties: int
    modulus: int
    model_version: str
    feature_secret_ids: tuple[str, ...]
    receiver_id: str = "insurer"

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id must be a nonempty string")
        if type(self.expires_at) is not int:
            raise TypeError("expires_at must be an integer Unix timestamp")
        if any(type(value) is not int for value in (
            self.threshold, self.num_parties, self.modulus
        )):
            raise TypeError("protocol parameters must be integers")
        if not 1 <= self.threshold <= self.num_parties < self.modulus:
            raise ValueError("invalid sharing parameters")
        if 2 * (self.threshold - 1) >= self.num_parties:
            raise ValueError("BGW multiplication requires an honest majority")
        if not is_prime(self.modulus):
            raise ValueError("modulus must be prime")
        if not isinstance(self.model_version, str) or not self.model_version:
            raise ValueError("model_version must be a nonempty string")
        if type(self.feature_secret_ids) is not tuple or (
            set(self.feature_secret_ids) != set(FEATURE_OWNERS)
            or len(self.feature_secret_ids) != len(FEATURE_OWNERS)
        ):
            raise ValueError("feature_secret_ids must match the current audit features")
        if self.receiver_id != "insurer":
            raise ValueError("only insurer may receive the audit result")

    def to_payload(self) -> dict[str, object]:
        """Serialize the manifest for all party services.

        向所有 party 发送相同任务清单，避免各节点采用不同协议参数。
        Send the same manifest to every party to keep protocol parameters aligned.
        """

        return {
            "session_id": self.session_id,
            "expires_at": self.expires_at,
            "threshold": self.threshold,
            "num_parties": self.num_parties,
            "modulus": self.modulus,
            "model_version": self.model_version,
            "feature_secret_ids": list(self.feature_secret_ids),
            "receiver_id": self.receiver_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> TaskManifest:
        """Parse a network manifest with strict field types.

        严格解析网络任务字段，避免布尔值等被当作整数协议参数。
        Strictly parse network fields so booleans cannot stand in for integers.
        """

        expected = {
            "session_id", "expires_at", "threshold", "num_parties", "modulus",
            "model_version", "feature_secret_ids", "receiver_id",
        }
        if set(payload) != expected:
            raise ValueError("task manifest fields are incomplete or unexpected")
        features = payload["feature_secret_ids"]
        if not isinstance(features, list) or not all(
            isinstance(value, str) for value in features
        ):
            raise TypeError("feature_secret_ids must be a list of strings")
        return cls(
            session_id=payload["session_id"],
            expires_at=payload["expires_at"],
            threshold=payload["threshold"],
            num_parties=payload["num_parties"],
            modulus=payload["modulus"],
            model_version=payload["model_version"],
            feature_secret_ids=tuple(features),
            receiver_id=payload["receiver_id"],
        )


@dataclass
class TaskRegistry:
    """Thread-safe manifests owned by one HTTP party service.

    单个 HTTP party 服务的线程安全任务清单；不跨机构共享业务字段。
    Thread-safe manifests for one party, without cross-organization source data.
    """

    party_id: int
    _tasks: dict[str, TaskManifest] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def register(self, manifest: TaskManifest) -> bool:
        """Accept only a fresh task or an identical retry.

        仅接受新任务或完全相同的重试；冲突任务不能覆盖已有会话。
        Accept a new task or an identical retry; conflicting manifests never overwrite.
        """

        if self.party_id > manifest.num_parties:
            raise ValueError("task does not include this party")
        with self._lock:
            # 锁内检查有效期；过期标识不能通过重新注册延长或复活。
            # Check expiry under the lock; re-registration cannot renew an expired session.
            now = time()
            existing = self._tasks.get(manifest.session_id)
            if existing is not None and now >= existing.expires_at:
                raise TaskExpiredError("task has expired")
            if not now < manifest.expires_at <= now + MAX_TASK_LIFETIME_SECONDS:
                raise ValueError("task expiry must be within the next 24 hours")
            if existing is not None and existing != manifest:
                raise ValueError("session_id is already registered with another manifest")
            self._tasks[manifest.session_id] = manifest
            return existing is not None

    def require(self, session_id: str) -> TaskManifest:
        """Return a live registered task or fail before party state is used.

        在读取或修改 party shares 前要求会话存在且未过期。
        Require a live session before reading or mutating party shares.
        """

        with self._lock:
            manifest = self._tasks.get(session_id)
        if manifest is None:
            raise KeyError("task is not registered")
        if time() >= manifest.expires_at:
            raise TaskExpiredError("task has expired")
        return manifest
