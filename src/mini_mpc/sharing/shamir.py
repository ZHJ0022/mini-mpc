import secrets

from mini_mpc.math.field import FieldElement, is_prime
from mini_mpc.math.polynomial import Polynomial
from mini_mpc.sharing.share import Share


def validate_sharing_parameters(threshold: int, num_parties: int, modulus: int) -> None:
    """Validate the integer parameters shared by Shamir runtimes.

    统一校验 Shamir 参数，供分享、会话和客户端配置复用。
    """
    for name, value in (("threshold", threshold), ("num_parties", num_parties), ("modulus", modulus)):
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
    if threshold < 1:
        raise ValueError("threshold must be at least 1")
    if num_parties < threshold:
        raise ValueError("num_parties must be at least threshold")
    if modulus <= num_parties:
        raise ValueError("modulus must be greater than num_parties")
    if not is_prime(modulus):
        raise ValueError("modulus must be prime")


def share_secret(
    secret: int, threshold: int, num_parties: int, modulus: int
) -> list[Share]:
    """Create Shamir shares for a secret.

    使用 Shamir k-out-of-n 方案分享一个整数秘密。
    Share an integer secret using Shamir k-out-of-n secret sharing.

    ``threshold`` 表示重构秘密所需的最少份额数量 k。
    ``threshold`` is the minimum number of shares required for reconstruction.

    随机多项式的次数至多为 ``threshold - 1``。
    The random polynomial degree is at most ``threshold - 1``.
    """

    validate_sharing_parameters(threshold, num_parties, modulus)

    # 常数项是 secret，保证 Q(0) = secret。
    # The constant coefficient is the secret, so Q(0) = secret.
    coefficients = [FieldElement(secret, modulus)]
    for _ in range(threshold - 1):
        # Shamir 的隐私性依赖随机系数，必须使用密码学安全随机数。
        # Shamir privacy depends on random coefficients; use CSPRNG randomness.
        random_coefficient = secrets.randbelow(modulus)
        coefficients.append(FieldElement(random_coefficient, modulus))

    polynomial = Polynomial(coefficients)
    shares = []

    for party_id in range(1, num_parties + 1):
        shares.append(
            Share(
                party_id=party_id,
                value=polynomial.evaluate(party_id),
                threshold=threshold,
                num_parties=num_parties,
            )
        )

    return shares


def reconstruct_secret(shares: list[Share]) -> FieldElement:
    """Reconstruct a secret with Lagrange interpolation at x = 0.

    使用至少 ``threshold`` 个兼容 shares 在 x=0 处做 Lagrange 插值。
    Reconstruct by Lagrange interpolation at x=0 from at least ``threshold``
    compatible shares.

    重构会拒绝数量不足、重复 party id、不同有限域、不同阈值或不同总参与方数量。
    Reconstruction rejects too few shares, duplicate party ids, mixed fields,
    mixed thresholds, and mixed party counts.
    """

    if not shares:
        raise ValueError("at least one share is required")

    first_share = shares[0]
    if not isinstance(first_share, Share):
        raise TypeError("shares must be Share instances")

    party_ids = set()
    threshold = first_share.threshold
    num_parties = first_share.num_parties
    modulus = first_share.value.modulus

    if len(shares) < threshold:
        raise ValueError("not enough shares to reconstruct secret")

    for share in shares:
        if not isinstance(share, Share):
            raise TypeError("shares must be Share instances")
        if share.party_id in party_ids:
            raise ValueError("party_id values must be unique")
        if share.value.modulus != modulus:
            raise ValueError("all shares must use the same modulus")
        if share.threshold != threshold:
            raise ValueError("all shares must use the same threshold")
        if share.num_parties != num_parties:
            raise ValueError("all shares must use the same num_parties")
        party_ids.add(share.party_id)

    secret = FieldElement(0, modulus)
    coefficients = lagrange_coefficients_at_zero(
        tuple(share.party_id for share in shares),
        modulus,
    )

    for share in shares:
        secret = secret + share.value * coefficients[share.party_id]

    return secret


def lagrange_coefficients_at_zero(
    party_ids: tuple[int, ...],
    modulus: int,
) -> dict[int, FieldElement]:
    """Return Lagrange coefficients for interpolation at x = 0.

    返回在 x=0 插值所需的 Lagrange 系数。
    Return Lagrange coefficients used to interpolate a value at x=0.
    """

    if not party_ids:
        raise ValueError("party_ids must not be empty")
    if len(set(party_ids)) != len(party_ids):
        raise ValueError("party_ids must be unique")

    coefficients = {}
    for party_id in party_ids:
        basis = FieldElement(1, modulus)
        for other_id in party_ids:
            if other_id != party_id:
                # 计算 L_j(0) = product_m!=j (-x_m) / (x_j - x_m)。
                # Compute L_j(0) = product_m!=j (-x_m) / (x_j - x_m).
                basis = basis * FieldElement(-other_id, modulus)
                basis = basis / FieldElement(party_id - other_id, modulus)
        coefficients[party_id] = basis
    return coefficients
