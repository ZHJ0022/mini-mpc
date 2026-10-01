from collections.abc import Mapping, Sequence

from mini_mpc.math.field import FieldElement
from mini_mpc.protocol.classification import (
    classify_threshold_level,
    validate_threshold_classifier,
)
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from mini_mpc.sharing.share import Share


def secure_sum(
    values: Sequence[int], *, threshold: int, num_parties: int, modulus: int
) -> FieldElement:
    """Compute and reconstruct a sum in the single-process reference path.

    单进程参考路径模拟份额分发、线性聚合和重构，用于示例与数学对照。
    This path simulates distribution, aggregation and reconstruction for references.
    """

    output_shares = secure_sum_shares(
        values, threshold=threshold, num_parties=num_parties, modulus=modulus
    )
    return reconstruct_secret(output_shares[:threshold])


def secure_sum_shares(
    values: Sequence[int], *, threshold: int, num_parties: int, modulus: int
) -> list[Share]:
    """Return output shares for a private integer sum.

    返回多个私有整数总和的 output shares，而不是立即重构结果。
    Return output shares for the sum of private integers instead of immediately
    reconstructing the result.

    协议层产出结果份额；披露层决定接收者及重构权限。
    This API supports selective output disclosure: the protocol layer produces
    result shares, and the disclosure layer decides who receives enough shares.
    """

    if not values:
        raise ValueError("values must not be empty")

    # 每个私有输入独立分享；同一 party 会收到每个输入的一份 share。
    # Each private input is shared independently; each party receives one share per input.
    shared_values = [
        share_secret(value, threshold=threshold, num_parties=num_parties, modulus=modulus)
        for value in values
    ]

    # 每个 party 只本地聚合自己的 shares，输出仍保持 secret-shared 状态。
    # Each party locally aggregates only its own shares, so the output remains secret-shared.
    return _sum_by_party(shared_values)


def secure_weighted_sum(
    values: Sequence[int],
    weights: Sequence[int],
    *,
    threshold: int,
    num_parties: int,
    modulus: int,
) -> FieldElement:
    """Compute a public-weighted sum with Shamir MPC simulation.

    使用 Shamir MPC 模拟计算公开权重下的私有整数加权和。
    Compute a public-weighted sum of private integers with Shamir MPC
    simulation.

    协议层只提供通用线性计算；具体业务特征、权重和阈值由应用层配置。
    The protocol layer provides generic linear computation; application code
    selects the business features, weights, and thresholds.

    这种分层让后续评估风险模型时可以调整应用层配置，而不改变 MPC 核心协议。
    This separation keeps model evaluation and tuning in the application layer
    without changing the MPC core protocol.
    """

    output_shares = secure_weighted_sum_shares(
        values,
        weights,
        threshold=threshold,
        num_parties=num_parties,
        modulus=modulus,
    )
    return reconstruct_secret(output_shares[:threshold])


def secure_weighted_sum_shares(
    values: Sequence[int],
    weights: Sequence[int],
    *,
    threshold: int,
    num_parties: int,
    modulus: int,
) -> list[Share]:
    """Return output shares for a public-weighted private sum.

    返回公开权重线性和的 output shares，而不是立即重构结果。
    Return output shares for a public-weighted linear sum instead of
    immediately reconstructing the result.

    协议层只处理 values 和 weights 的线性组合；特征顺序、权重含义和风险阈值由应用层维护。
    The protocol layer only evaluates the linear combination of values and
    weights; feature order, weight meaning, and risk thresholds belong to the
    application layer.
    """

    validate_values_and_weights(values, weights)

    # 将每个私有输入独立分享给同一组模拟 MPC parties。
    # Independently share each private input among the same simulated MPC parties.
    shared_values = [
        share_secret(value, threshold=threshold, num_parties=num_parties, modulus=modulus)
        for value in values
    ]

    # 每个 party 对自己的 share 乘以公开权重；这里没有私有值相乘。
    # Each party scales its own share by the public weight; no private-private multiplication is used.
    weighted_shared_values = []
    for shares, weight in zip(shared_values, weights, strict=True):
        weighted_shared_values.append([share * weight for share in shares])

    # 按 party 聚合加权 shares；调用方决定何时重构以及向谁披露。
    # Aggregate weighted shares by party; the caller decides when and to whom they are reconstructed.
    return _sum_by_party(weighted_shared_values)


def classify_threshold_level_shares(
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
    """Return shares of a threshold-classified level code.

    将 secret-shared 小整数分数分类为等级编码，并返回新的等级 shares。
    Classify a secret-shared small integer score into a level code and return
    fresh level shares.

    这是 reference-only 单进程 wrapper；默认 backend 已使用分布式安全查表路径。
    This is a reference-only single-process wrapper; the default backend uses
    the distributed secure-lookup path.
    """

    validate_threshold_classifier(
        thresholds=thresholds,
        level_codes=level_codes,
        score_range=score_range,
        default_level=default_level,
    )

    if not score_shares:
        raise ValueError("score_shares must not be empty")

    reconstruction_threshold = score_shares[0].threshold
    score = reconstruct_secret(list(score_shares[:reconstruction_threshold])).value
    minimum_score, maximum_score = score_range
    if not minimum_score <= score <= maximum_score:
        raise ValueError("score is outside the configured score range")

    level = classify_threshold_level(
        score,
        thresholds=thresholds,
        default_level=default_level,
    )
    return share_secret(
        level_codes[level],
        threshold=threshold,
        num_parties=num_parties,
        modulus=modulus,
    )


def validate_values_and_weights(values: Sequence[int], weights: Sequence[int]) -> None:
    """Validate public-weight linear inputs for reference and runtime paths.

    参考协议与 backend 共用线性输入校验；通用域运算允许负整数。
    """
    if not values:
        raise ValueError("values must not be empty")
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    for value in values:
        if type(value) is not int:
            raise TypeError("values must be integers")
    for weight in weights:
        if type(weight) is not int:
            raise TypeError("weights must be integers")


def _sum_by_party(shared_values: Sequence[Sequence[Share]]) -> list[Share]:
    """Sum a matrix of shares by party id.

    按 party_id 对 share 矩阵逐列求和，得到每个 party 的聚合 share。
    Sum a matrix of shares column-wise by party id to obtain one aggregate
    share per party.
    """

    if not shared_values:
        raise ValueError("shared_values must not be empty")

    num_parties = len(shared_values[0])
    if num_parties == 0:
        raise ValueError("each shared value must contain at least one share")

    for shares in shared_values:
        if len(shares) != num_parties:
            raise ValueError("all shared values must have the same party count")

    summed_shares = []
    for party_index in range(num_parties):
        party_sum = shared_values[0][party_index]
        for shares in shared_values[1:]:
            party_sum = party_sum + shares[party_index]
        summed_shares.append(party_sum)

    return summed_shares
