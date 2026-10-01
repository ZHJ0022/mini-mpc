from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mini_mpc.math.polynomial import interpolate_coefficients
from mini_mpc.runtime.computation_plan import polynomial_steps
from mini_mpc.network.http_server import reshare_secret_id
from mini_mpc.network.transport import MPCPartyEndpoint, PartyTransport
from mini_mpc.network.task_registry import TaskManifest
from mini_mpc.protocol.classification import (
    threshold_classification_table,
    validate_threshold_classifier,
)
from mini_mpc.sharing.shamir import share_secret, validate_sharing_parameters
from mini_mpc.sharing.share import Share


@dataclass(frozen=True)
class SharingConfig:
    """Remote sharing configuration for one audit session.

    一次稽核任务的远程 sharing 配置，包含协议参数和 MPC party 客户端。
    Remote sharing configuration for one audit session, including protocol
    parameters and MPC party clients.
    """

    session_id: str
    threshold: int
    num_parties: int
    modulus: int
    party_clients: tuple[PartyTransport, ...]

    def __post_init__(self) -> None:
        """Validate sharing parameters before sending shares.

        发送 shares 前校验参数，避免把 shares 发到不完整的 party 集合。
        Validate sharing parameters before sending shares so output is not sent
        to an incomplete party set.
        """

        if not self.session_id:
            raise ValueError("session_id must not be empty")
        validate_sharing_parameters(self.threshold, self.num_parties, self.modulus)
        if len(self.party_clients) != self.num_parties:
            raise ValueError("party_clients must contain one client per party")


def remote_register_task(
    sharing: SharingConfig,
    *,
    expires_at: int,
    model_version: str,
    feature_secret_ids: Sequence[str],
) -> None:
    """Send an identical task manifest to every remote party.

    医保向所有 party 发送同一任务清单；失败后可用相同清单重试。
    The insurer sends one manifest to all parties; an identical retry is safe.
    """

    manifest = TaskManifest(
        session_id=sharing.session_id,
        expires_at=expires_at,
        threshold=sharing.threshold,
        num_parties=sharing.num_parties,
        modulus=sharing.modulus,
        model_version=model_version,
        feature_secret_ids=tuple(feature_secret_ids),
    )
    for client in _sorted_party_clients(sharing):
        client.register_task(manifest)


def submit_feature_shares(
    secret_id: str,
    value: int,
    sharing: SharingConfig,
) -> None:
    """Share one local feature and send shares to remote MPC parties.

    将一个本地特征拆成 shares，并按 party_id 发送给远程 MPC parties。
    Share one local feature and send each share to the matching remote MPC
    party by party_id.
    """

    shares = share_secret(
        value,
        threshold=sharing.threshold,
        num_parties=sharing.num_parties,
        modulus=sharing.modulus,
    )
    clients_by_party = {
        client.endpoint.party_id: client for client in sharing.party_clients
    }
    if set(clients_by_party) != set(range(1, sharing.num_parties + 1)):
        raise ValueError("party_clients must cover party ids 1..num_parties")

    # 数据节点只发送每个 party 对应的 share，不把完整 shares 集合交给某一个 party。
    # The data node sends each party only its own share, not the full share set.
    for share in shares:
        clients_by_party[share.party_id].submit_share(
            sharing.session_id,
            secret_id,
            share,
        )
    # 输入方只确认发送完成，不将完整 shares 集合作为接口结果交给调用方。
    # Report submission by successful return, without exposing the full set.


def remote_weighted_sum_shares(
    sharing: SharingConfig,
    input_secret_ids: Sequence[str],
    weights: Sequence[int],
    output_secret_id: str,
) -> list[Share]:
    """Run public-weighted sum across remote MPC party services.

    调用远程 MPC party 服务执行公开权重求和；每个 party 只计算自己的 output share。
    Run a public-weighted sum across remote MPC party services; each party
    computes only its own output share.
    """

    output_shares = []
    for client in _sorted_party_clients(sharing):
        output_shares.append(
            client.compute_weighted_sum_share(
                sharing.session_id,
                input_secret_ids,
                weights,
                output_secret_id,
            )
        )
    return output_shares


def remote_compute_weighted_sum(
    sharing: SharingConfig,
    input_secret_ids: Sequence[str],
    weights: Sequence[int],
    output_secret_id: str,
) -> None:
    """Store party-local weighted sums without collecting any score shares.

    业务调度只发送公开权重和 secret 标识，party 响应不包含分数 share。
    The coordinator sends public weights and ids, then receives only acknowledgements.
    """

    for client in _sorted_party_clients(sharing):
        client.compute_weighted_sum(
            sharing.session_id, input_secret_ids, weights, output_secret_id
        )


def remote_submit_public_constant(
    sharing: SharingConfig,
    secret_id: str,
    value: int,
) -> list[Share]:
    """Submit a public constant to all remote MPC parties.

    向所有远程 MPC parties 提交公开常量；每个 party 保存自己的零次 share。
    Submit a public constant to all remote MPC parties; each party stores its
    own degree-zero share.
    """

    remote_store_public_constant(sharing, secret_id, value)
    return remote_collect_shares(sharing, secret_id)


def remote_store_public_constant(
    sharing: SharingConfig,
    secret_id: str,
    value: int,
) -> None:
    """Store a public constant without fetching its shares.

    公开常量可由各 party 本地表示，调度方无需读取保存后的 shares。
    Each party stores the public constant; no share fetch is required.
    """

    for client in sharing.party_clients:
        client.submit_public_constant(
            sharing.session_id,
            secret_id,
            value,
            threshold=sharing.threshold,
            num_parties=sharing.num_parties,
            modulus=sharing.modulus,
        )


def remote_multiply_shares(
    sharing: SharingConfig,
    left_secret_id: str,
    right_secret_id: str,
    output_secret_id: str,
) -> list[Share]:
    """Multiply two remote shared values with BGW-style reshare messages.

    通过 BGW-style reshare 消息乘两个远程 shared values。
    Multiply two remote shared values using BGW-style reshare messages.
    """

    remote_compute_multiply(sharing, left_secret_id, right_secret_id, output_secret_id)
    return remote_collect_shares(sharing, output_secret_id)


def remote_compute_multiply(
    sharing: SharingConfig,
    left_secret_id: str,
    right_secret_id: str,
    output_secret_id: str,
) -> None:
    """Run BGW reshare without collecting the reduced product shares.

    每个 party 直接向 peers 发送 reshares；调度方只接收完成状态。
    Parties send reshares directly to peers; the coordinator sees acknowledgements.
    """

    if 2 * (sharing.threshold - 1) >= sharing.num_parties:
        raise ValueError("BGW multiplication requires 2 * (threshold - 1) < num_parties")

    endpoints = _party_endpoints(sharing)
    for client in _sorted_party_clients(sharing):
        client.distribute_multiplication_reshares(
            sharing.session_id,
            left_secret_id,
            right_secret_id,
            output_secret_id,
            endpoints,
        )

    # 发送方已乘 Lagrange 系数，接收方只需把来自所有 owner 的 reshares 相加。
    # Senders already apply Lagrange coefficients, so receivers only sum all owner reshares.
    reshare_ids = tuple(
        reshare_secret_id(output_secret_id, party_id)
        for party_id in range(1, sharing.num_parties + 1)
    )
    remote_compute_weighted_sum(
        sharing,
        reshare_ids,
        tuple(1 for _ in reshare_ids),
        output_secret_id,
    )


def remote_evaluate_public_polynomial(
    sharing: SharingConfig,
    value_secret_id: str,
    coefficients: Sequence[int],
    output_secret_id: str,
) -> list[Share]:
    """Evaluate a public polynomial over a remote shared value.

    在远程 shared value 上用 Horner 方法计算公开多项式。
    Evaluate a public polynomial over a remote shared value with Horner's
    method.
    """

    remote_compute_public_polynomial(
        sharing, value_secret_id, coefficients, output_secret_id
    )
    return remote_collect_shares(sharing, output_secret_id)


def remote_compute_public_polynomial(
    sharing: SharingConfig,
    value_secret_id: str,
    coefficients: Sequence[int],
    output_secret_id: str,
) -> None:
    """Evaluate a public polynomial while all intermediate shares stay at parties.

    Horner 每轮仅传操作指令；乘积和累加值的 shares 不返回调度方。
    Each Horner step sends operations only; intermediate shares remain at parties.
    """

    # 与 party 使用同一公开指令表；乘法内部仍完成 reshare 求和。
    # Use the same public steps as parties; multiplication performs its reshare sum.
    for step in polynomial_steps(
        value_secret_id, tuple(coefficients), output_secret_id, sharing.num_parties
    ):
        if step.kind == "constant":
            remote_store_public_constant(sharing, step.output_id, step.value)
        elif step.kind == "multiply":
            remote_compute_multiply(sharing, *step.input_ids, step.output_id)
        else:
            # BGW reshare 求和已由 remote_compute_multiply 完成；该步骤保留在计划中供 party 校验。
            # remote_compute_multiply already sums BGW reshares; retain that step for party authorization.
            reshare_ids = tuple(
                reshare_secret_id(step.output_id, party_id)
                for party_id in range(1, sharing.num_parties + 1)
            )
            if step.input_ids == reshare_ids:
                continue
            remote_compute_weighted_sum(
                sharing, step.input_ids, step.weights, step.output_id
            )


def remote_classify_level_shares(
    sharing: SharingConfig,
    score_secret_id: str,
    *,
    thresholds: Mapping[str, int],
    level_codes: Mapping[str, int],
    score_range: tuple[int, int],
    default_level: str = "none",
) -> list[Share]:
    """Classify remote score shares into remote risk-level shares.

    将远程 score shares 分类为远程 risk-level shares，不重构 risk_score。
    Classify remote score shares into remote risk-level shares without
    reconstructing risk_score.
    """

    remote_compute_level(
        sharing,
        score_secret_id,
        thresholds=thresholds,
        level_codes=level_codes,
        score_range=score_range,
        default_level=default_level,
    )
    return remote_collect_shares(sharing, "risk_level")


def remote_compute_level(
    sharing: SharingConfig,
    score_secret_id: str,
    *,
    thresholds: Mapping[str, int],
    level_codes: Mapping[str, int],
    score_range: tuple[int, int],
    default_level: str = "none",
) -> None:
    """Convert stored score shares into stored level shares without fetching score.

    分数和所有中间 share 留在 party；只有最终等级可单独交付。
    Score and intermediate shares remain at parties for final level delivery.
    """

    validate_threshold_classifier(
        thresholds=thresholds,
        level_codes=level_codes,
        score_range=score_range,
        default_level=default_level,
    )
    table = threshold_classification_table(
        thresholds=thresholds,
        level_codes=level_codes,
        score_range=score_range,
        default_level=default_level,
    )
    coefficients = interpolate_coefficients(table, sharing.modulus)
    remote_compute_public_polynomial(
        sharing,
        score_secret_id,
        tuple(coefficient.value for coefficient in coefficients),
        "risk_level",
    )


def remote_collect_shares(
    sharing: SharingConfig,
    secret_id: str,
) -> list[Share]:
    """Collect one share from each remote MPC party.

    从每个远程 MPC party 收集同一个 secret 的一份 share。
    Collect one share for the same secret from each remote MPC party.
    """

    return [
        client.get_share(sharing.session_id, secret_id)
        for client in _sorted_party_clients(sharing)
    ]


def remote_collect_result_shares(sharing: SharingConfig) -> list[Share]:
    """Collect only final level shares through the authorized result endpoint.

    正式业务仅从结果接口领取等级 shares，不提供任意 secret 标识。
    Business collection uses the result endpoint without a caller-chosen secret id.
    """

    return [
        client.get_result_share(sharing.session_id)
        for client in _sorted_party_clients(sharing)
    ]


def _party_endpoints(sharing: SharingConfig) -> tuple[MPCPartyEndpoint, ...]:
    """Return sorted party endpoints for peer-to-peer reshare delivery.

    返回排序后的 party endpoints，用于 peer-to-peer reshare 投递。
    """

    return tuple(client.endpoint for client in _sorted_party_clients(sharing))


def _sorted_party_clients(sharing: SharingConfig) -> list[PartyTransport]:
    return sorted(sharing.party_clients, key=lambda item: item.endpoint.party_id)
