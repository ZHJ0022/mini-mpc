from collections.abc import Sequence

from mini_mpc.protocol.reference import validate_values_and_weights
from mini_mpc.sharing.share import Share



def validate_score_shares(
    score_shares: Sequence[Share],
    *,
    threshold: int,
    num_parties: int,
    modulus: int,
) -> None:
    """Validate one full set of score shares for distributed classification.

    校验分布式等级分类所需的一整组 score shares。
    Validate the full score-share set required by distributed classification.
    """

    if len(score_shares) != num_parties:
        raise ValueError("score_shares must contain one share per party")
    seen_party_ids = set()
    for share in score_shares:
        if not isinstance(share, Share):
            raise TypeError("score_shares must contain Share instances")
        if share.party_id in seen_party_ids:
            raise ValueError("score_shares party_id values must be unique")
        if share.threshold != threshold:
            raise ValueError("score_shares must use the configured threshold")
        if share.num_parties != num_parties:
            raise ValueError("score_shares must use the configured num_parties")
        if share.value.modulus != modulus:
            raise ValueError("score_shares must use the configured modulus")
        seen_party_ids.add(share.party_id)

    if seen_party_ids != set(range(1, num_parties + 1)):
        raise ValueError("score_shares must cover all configured parties")
