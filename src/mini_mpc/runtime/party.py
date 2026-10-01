from dataclasses import dataclass, field
from _thread import LockType
from threading import Lock

from mini_mpc.sharing.share import Share


ShareKey = tuple[str, str]


class ShareConflictError(ValueError):
    """An immutable share key already contains different content.

    不可变份额标识已保存不同内容。
    The immutable share key already holds different content.
    """


@dataclass
class ShareStore:
    """Store shares owned by one MPC party.

    保存单个 MPC party 持有的 shares。
    Store only the shares owned by one MPC party.
    """

    party_id: int
    _shares: dict[ShareKey, Share] = field(default_factory=dict, repr=False)
    _lock: LockType = field(default_factory=Lock, repr=False)

    def put(self, session_id: str, secret_id: str, share: Share) -> None:
        """Save one share after validating ownership.

        校验 party_id 后保存 share。
        Store a share after checking that it belongs to this party.
        """

        _validate_identifier(session_id, "session_id")
        _validate_identifier(secret_id, "secret_id")
        if share.party_id != self.party_id:
            raise ValueError("share does not belong to this party")
        key = (session_id, secret_id)
        with self._lock:
            existing = self._shares.get(key)
            if existing is not None and existing != share:
                # 同一 secret id 的内容不可被重写；相同请求可安全重试。
                # A secret id is immutable; identical network retries remain safe.
                raise ShareConflictError("conflicting share already stored")
            self._shares[key] = share

    def get(self, session_id: str, secret_id: str) -> Share:
        """Return a stored share.

        返回已保存的 share。
        """

        _validate_identifier(session_id, "session_id")
        _validate_identifier(secret_id, "secret_id")
        with self._lock:
            try:
                return self._shares[(session_id, secret_id)]
            except KeyError as exc:
                raise KeyError("share not found") from exc

    def keys(self) -> tuple[ShareKey, ...]:
        """Return stored share keys for inspection.

        返回 share key，用于测试和检查。
        Return share keys for tests and inspection.
        """

        with self._lock:
            return tuple(self._shares.keys())


@dataclass
class MPCParty:
    """In-memory MPC party with isolated share storage.

    内存版 MPC party，只保存自己的 shares。
    In-memory MPC party that stores only its own shares.
    """

    party_id: int
    store: ShareStore = field(init=False)

    def __post_init__(self) -> None:
        if type(self.party_id) is not int:
            raise TypeError("party_id must be an integer")
        if self.party_id <= 0:
            raise ValueError("party_id must be positive")
        self.store = ShareStore(self.party_id)

    def receive_share(self, session_id: str, secret_id: str, share: Share) -> None:
        """Receive one routed share.

        接收路由到本 party 的 share。
        Receive one share routed to this party.
        """

        self.store.put(session_id, secret_id, share)

    def get_share(self, session_id: str, secret_id: str) -> Share:
        """Return one local share.

        返回本 party 的一个本地 share。
        Return one local share owned by this party.
        """

        return self.store.get(session_id, secret_id)

    def compute_weighted_sum_share(
        self,
        session_id: str,
        input_secret_ids: tuple[str, ...],
        weights: tuple[int, ...],
        output_secret_id: str,
    ) -> Share:
        """Compute one party-local weighted output share.

        只使用本 party 的 shares 计算加权 output share。
        Compute the weighted output share using only this party's shares.
        """

        if not input_secret_ids:
            raise ValueError("input_secret_ids must not be empty")
        if len(input_secret_ids) != len(weights):
            raise ValueError("input_secret_ids and weights must have the same length")

        output_share = self.get_share(session_id, input_secret_ids[0]) * weights[0]
        for secret_id, weight in zip(input_secret_ids[1:], weights[1:], strict=True):
            output_share = output_share + self.get_share(session_id, secret_id) * weight

        self.store.put(session_id, output_secret_id, output_share)
        return output_share


def _validate_identifier(value: str, field_name: str) -> None:
    """Validate a non-empty string identifier.

    校验非空字符串标识。
    """

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
