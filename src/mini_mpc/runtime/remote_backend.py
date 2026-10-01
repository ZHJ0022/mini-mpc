from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mini_mpc.math.field import FieldElement
from mini_mpc.network.transport import PartyTransport
from mini_mpc.network.config import load_remote_client_config
from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.protocol.classification import validate_threshold_classifier
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy, reveal_to_receiver
from mini_mpc.runtime.remote_protocol import (
    SharingConfig,
    remote_classify_level_shares,
    remote_collect_shares,
    remote_collect_result_shares,
    remote_compute_level,
    remote_compute_weighted_sum,
    remote_weighted_sum_shares,
    submit_feature_shares,
)
from mini_mpc.runtime.validation import validate_score_shares, validate_values_and_weights
from mini_mpc.sharing.share import Share


@dataclass(frozen=True)
class RemoteComputationState:
    """Remote secret metadata tracked by the backend.

    backend 记录的远程 secret 元数据。

    远程 party 已经保存了 shares；这里保存的是 session/secret 标识，不保存明文分数。
    Remote parties already store shares; this state stores session/secret ids,
    not plaintext scores.
    """

    sharing: SharingConfig
    score_secret_id: str


class RemoteShamirBackend:
    """Remote Shamir backend using PartyTransport clients.

    使用 PartyTransport clients 的远程 Shamir backend。

    该 backend 复用 HTTPS party 协议组合，让应用层通过 `MPCBackend` 接口使用远程 party。
    This backend reuses the HTTPS party protocol so application code can use
    remote parties through the `MPCBackend` interface.
    """

    def __init__(
        self,
        party_clients: Sequence[PartyTransport],
        *,
        input_secret_prefix: str = "input",
        score_secret_id: str = "weighted_sum",
    ) -> None:
        self.party_clients = tuple(party_clients)
        self.input_secret_prefix = input_secret_prefix
        self.score_secret_id = score_secret_id
        self.sessions: dict[str, RemoteComputationState] = {}
        self._score_states_by_share_key: dict[tuple[tuple[int, int], ...], RemoteComputationState] = {}

    @classmethod
    def from_config(cls, path: str) -> "RemoteShamirBackend":
        """Build the remote backend from the same client config used by nodes.

        从运行配置构造 party clients；业务代码不硬编码 endpoint 或 CA。
        Build party clients from deployment config, not hard-coded endpoints.
        """

        config = load_remote_client_config(path)
        clients = tuple(
            MPCPartyHttpClient(endpoint, tls_config=config.tls)
            for endpoint in config.endpoints
        )
        return cls(clients)

    def compute_submitted_level_shares(
        self,
        session_id: str,
        input_secret_ids: Sequence[str],
        weights: Sequence[int],
        *,
        thresholds: Mapping[str, int],
        level_codes: Mapping[str, int],
        score_range: tuple[int, int],
        threshold: int,
        num_parties: int,
        modulus: int,
    ) -> list[Share]:
        """Classify features already submitted by their data-owning nodes.

        远程业务入口只引用 party 内已保存的 shares，不接受明文特征。
        The remote business path references shares already stored at parties;
        it does not accept plaintext features from the coordinator.
        """

        if not input_secret_ids or len(input_secret_ids) != len(weights):
            raise ValueError("input_secret_ids and weights must have the same nonzero length")
        validate_threshold_classifier(
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
            default_level="none",
        )
        sharing = self._sharing(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )
        # 正常业务调度只接收完成状态，不读取 score 或 Horner 中间 shares。
        # The business path receives acknowledgements, never score or Horner shares.
        remote_compute_weighted_sum(
            sharing, input_secret_ids, weights, self.score_secret_id
        )
        remote_compute_level(
            sharing,
            self.score_secret_id,
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
        )
        # 业务路径走服务端授权的最终结果接口，无法指定其他 secret id。
        # The business path uses the authorized result endpoint without choosing a secret id.
        return remote_collect_result_shares(sharing)

    def weighted_sum_shares(
        self,
        values: Sequence[int],
        weights: Sequence[int],
        *,
        threshold: int,
        num_parties: int,
        modulus: int,
        session_id: str = "default",
    ) -> list[Share]:
        """Return remote output shares for a public-weighted sum.

        通过远程 party-local 线性计算返回 output shares。
        Return output shares through remote party-local linear computation.
        """

        validate_values_and_weights(values, weights)
        sharing = self._sharing(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )

        input_secret_ids = tuple(
            f"{self.input_secret_prefix}_{index}" for index in range(len(values))
        )
        for secret_id, value in zip(input_secret_ids, values, strict=True):
            submit_feature_shares(secret_id, value, sharing)

        score_shares = remote_weighted_sum_shares(
            sharing,
            input_secret_ids,
            tuple(weights),
            self.score_secret_id,
        )
        state = RemoteComputationState(sharing=sharing, score_secret_id=self.score_secret_id)
        self.sessions[session_id] = state
        self._score_states_by_share_key[_shares_key(score_shares)] = state
        return score_shares

    def classify_level_shares(
        self,
        score_shares: Sequence[Share],
        *,
        thresholds: Mapping[str, int],
        level_codes: Mapping[str, int],
        score_range: tuple[int, int],
        threshold: int,
        num_parties: int,
        modulus: int,
        default_level: str = "none",
    ) -> list[Share]:
        """Return remote shares of a risk-level code.

        返回远程风险等级编码 shares。
        Return remote shares of the risk-level code.
        """

        validate_threshold_classifier(
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
            default_level=default_level,
        )
        validate_score_shares(
            score_shares,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )
        state = self._state_for_score_shares(
            score_shares,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )

        return remote_classify_level_shares(
            state.sharing,
            state.score_secret_id,
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
            default_level=default_level,
        )

    def disclose_to_receiver(
        self,
        output_shares: Sequence[Share],
        policy: OutputDisclosurePolicy,
        receiver_id: str,
    ) -> FieldElement:
        """Reveal output shares through the disclosure layer.

        通过披露层重构授权输出。
        Reveal authorized output through the disclosure layer.
        """

        return reveal_to_receiver(output_shares, policy, receiver_id)

    def _sharing(
        self,
        *,
        session_id: str,
        threshold: int,
        num_parties: int,
        modulus: int,
    ) -> SharingConfig:
        return SharingConfig(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
            party_clients=self.party_clients,
        )

    def _state_for_score_shares(
        self,
        score_shares: Sequence[Share],
        *,
        threshold: int,
        num_parties: int,
        modulus: int,
    ) -> RemoteComputationState:
        share_key = _shares_key(score_shares)
        if share_key in self._score_states_by_share_key:
            return self._score_states_by_share_key[share_key]

        # 兼容独立调用：如果 score shares 不是本 backend 刚计算的，就把它们按 party_id
        # 提交到一个新的远程 session，再执行分类。这里仍不重构 risk_score。
        # Support standalone classification: if score shares were not produced
        # by this backend, submit them to a new remote session by party_id. The
        # risk_score is still never reconstructed.
        session_id = f"classification_{len(self.sessions) + 1}"
        sharing = self._sharing(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )
        clients_by_party = {
            client.endpoint.party_id: client for client in sharing.party_clients
        }
        for share in score_shares:
            clients_by_party[share.party_id].submit_share(
                sharing.session_id,
                self.score_secret_id,
                share,
            )
        state = RemoteComputationState(sharing=sharing, score_secret_id=self.score_secret_id)
        self.sessions[session_id] = state
        self._score_states_by_share_key[share_key] = state
        return state


def _shares_key(shares: Sequence[Share]) -> tuple[tuple[int, int], ...]:
    return tuple(
        (share.party_id, share.value.value)
        for share in sorted(shares, key=lambda item: item.party_id)
    )
