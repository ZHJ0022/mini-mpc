from dataclasses import dataclass, field

from mini_mpc.math.field import FieldElement
from mini_mpc.runtime.party import MPCParty
from mini_mpc.sharing.shamir import (
    lagrange_coefficients_at_zero, share_secret, validate_sharing_parameters,
)
from mini_mpc.sharing.share import Share


@dataclass
class ProtocolSession:
    """Coordinate one in-memory distributed MPC run.

    协调一次内存版分布式 MPC 运行。
    """

    session_id: str
    threshold: int
    num_parties: int
    modulus: int
    parties: dict[int, MPCParty] = field(init=False)

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        validate_sharing_parameters(self.threshold, self.num_parties, self.modulus)
        self.parties = {
            party_id: MPCParty(party_id)
            for party_id in range(1, self.num_parties + 1)
        }

    def submit_secret(self, secret_id: str, value: int) -> None:
        """Share one secret and route each share to its party.

        拆分一个 secret，并把每份 share 路由到对应 party。
        Share one secret and route each share to the matching party.
        """

        for share in share_secret(
            value,
            threshold=self.threshold,
            num_parties=self.num_parties,
            modulus=self.modulus,
        ):
            self.parties[share.party_id].receive_share(
                self.session_id,
                secret_id,
                share,
            )

    def compute_weighted_sum(
        self,
        input_secret_ids: tuple[str, ...],
        weights: tuple[int, ...],
        output_secret_id: str,
    ) -> list[Share]:
        """Ask every party to compute its weighted output share.

        调度所有 parties 计算各自的加权 output share。
        Ask all parties to compute their local weighted output shares.
        """

        output_shares = []
        for party_id in sorted(self.parties):
            output_shares.append(
                self.parties[party_id].compute_weighted_sum_share(
                    self.session_id,
                    input_secret_ids,
                    weights,
                    output_secret_id,
                )
            )
        return output_shares

    def submit_share(self, secret_id: str, share: Share) -> None:
        """Route an existing share to its owning party.

        将已有 share 路由到对应 party。
        """

        self.parties[share.party_id].receive_share(
            self.session_id,
            secret_id,
            share,
        )

    def submit_public_constant(self, secret_id: str, value: int) -> None:
        """Submit a public constant as degree-zero shares.

        将公开常量作为零次多项式 shares 提交。
        """

        for party_id, party in self.parties.items():
            party.receive_share(
                self.session_id,
                secret_id,
                Share(
                    party_id=party_id,
                    value=FieldElement(value, self.modulus),
                    threshold=self.threshold,
                    num_parties=self.num_parties,
                ),
            )

    def multiply_shares(
        self,
        left_secret_id: str,
        right_secret_id: str,
        output_secret_id: str,
    ) -> list[Share]:
        """Multiply two shared values with BGW degree reduction.

        使用 BGW 乘法，将分享次数降回至多 threshold - 1。
        Multiply shared values and reduce the degree to at most threshold - 1.
        """

        if 2 * (self.threshold - 1) >= self.num_parties:
            raise ValueError(
                "BGW multiplication requires 2 * (threshold - 1) < num_parties"
            )

        product_values = {
            party_id: party.get_share(self.session_id, left_secret_id).value
            * party.get_share(self.session_id, right_secret_id).value
            for party_id, party in self.parties.items()
        }
        coefficients = lagrange_coefficients_at_zero(
            tuple(sorted(self.parties)),
            self.modulus,
        )

        reduced_by_party: dict[int, Share | None] = {
            party_id: None for party_id in self.parties
        }
        for owner_id, product_value in product_values.items():
            # 每个 party 重新分享自己的局部乘积，再按 Lagrange 系数组合。
            # Each party reshares its local product, then recipients combine by Lagrange coefficients.
            reshares = share_secret(
                product_value.value,
                threshold=self.threshold,
                num_parties=self.num_parties,
                modulus=self.modulus,
            )
            coefficient = coefficients[owner_id].value
            for reshare in reshares:
                weighted_reshare = reshare * coefficient
                current = reduced_by_party[reshare.party_id]
                reduced_by_party[reshare.party_id] = (
                    weighted_reshare
                    if current is None
                    else current + weighted_reshare
                )

        output_shares = []
        for party_id in sorted(self.parties):
            output_share = reduced_by_party[party_id]
            if output_share is None:
                raise RuntimeError("missing reduced product share")
            self.parties[party_id].receive_share(
                self.session_id,
                output_secret_id,
                output_share,
            )
            output_shares.append(output_share)
        return output_shares

    def evaluate_public_polynomial(
        self,
        value_secret_id: str,
        coefficients: tuple[int, ...],
        output_secret_id: str,
    ) -> list[Share]:
        """Evaluate a public polynomial over a shared value.

        在 shared value 上计算公开多项式。
        Evaluate a public polynomial on a secret-shared value.
        """

        if not coefficients:
            raise ValueError("coefficients must not be empty")
        if len(coefficients) == 1:
            self.submit_public_constant(output_secret_id, coefficients[0])
            return self.collect_output_shares(output_secret_id)

        # Horner evaluation only uses public constants and shared multiplication.
        # Horner 求值只使用公开常量和 shared multiplication。
        accumulator_id = f"{output_secret_id}_constant_{len(coefficients) - 1}"
        self.submit_public_constant(accumulator_id, coefficients[-1])

        for index in range(len(coefficients) - 2, -1, -1):
            product_id = f"{output_secret_id}_product_{index}"
            constant_id = f"{output_secret_id}_constant_{index}"
            next_id = (
                output_secret_id if index == 0 else f"{output_secret_id}_step_{index}"
            )

            self.multiply_shares(accumulator_id, value_secret_id, product_id)
            self.submit_public_constant(constant_id, coefficients[index])
            self.compute_weighted_sum((product_id, constant_id), (1, 1), next_id)
            accumulator_id = next_id

        return self.collect_output_shares(output_secret_id)

    def collect_output_shares(self, output_secret_id: str) -> list[Share]:
        """Collect output shares from parties.

        从各 party 收集 output shares。
        Collect output shares from all parties.
        """

        return [
            self.parties[party_id].get_share(self.session_id, output_secret_id)
            for party_id in sorted(self.parties)
        ]
