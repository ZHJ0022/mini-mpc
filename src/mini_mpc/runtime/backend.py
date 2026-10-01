from collections.abc import Mapping, Sequence
from typing import Protocol

from mini_mpc.math.field import FieldElement
from mini_mpc.math.polynomial import interpolate_coefficients
from mini_mpc.protocol.classification import (
    threshold_classification_table,
    validate_threshold_classifier,
)
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy, reveal_to_receiver
from mini_mpc.runtime.session import ProtocolSession
from mini_mpc.runtime.validation import validate_score_shares, validate_values_and_weights
from mini_mpc.sharing.share import Share


class MPCBackend(Protocol):
    """Execution backend used by application code.

    应用层依赖的 MPC 执行后端接口。
    MPC execution interface used by application code.
    """

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
        """Classify inputs already shared to the parties, returning level shares.

        对已由数据方提交的 shares 计算等级；业务入口不接收明文特征。
        Data holders submit shares before this call; the business entry never
        receives their plaintext features.
        """

    def disclose_to_receiver(
        self,
        output_shares: Sequence[Share],
        policy: OutputDisclosurePolicy,
        receiver_id: str,
    ) -> FieldElement:
        """Reveal an output to an authorized receiver.

        向授权接收者披露输出。
        Reveal output to an authorized receiver.
        """


class InMemoryShamirBackend:
    """In-memory distributed backend using Shamir shares.

    使用 Shamir shares 的内存版分布式 backend。
    """

    def __init__(self) -> None:
        self.sessions: dict[str, ProtocolSession] = {}
        self._classification_coefficients: dict[tuple, tuple[int, ...]] = {}

    def submit_feature_share(
        self,
        session_id: str,
        secret_id: str,
        share: Share,
    ) -> None:
        """Route one precomputed share to its in-memory MPC party.

        模拟数据方直接提交一份 share；backend 不接收原始特征值。
        Simulate a data holder submitting one share without giving this
        backend the plaintext feature.
        """

        session = self.sessions.get(session_id)
        if session is None:
            session = ProtocolSession(
                session_id, share.threshold, share.num_parties, share.value.modulus
            )
            self.sessions[session_id] = session
        elif (session.threshold, session.num_parties, session.modulus) != (
            share.threshold, share.num_parties, share.value.modulus
        ):
            raise ValueError("sharing parameters do not match the session")
        party = session.parties[share.party_id]
        if (session_id, secret_id) in party.store.keys():
            raise ValueError("feature share already submitted")
        session.submit_share(secret_id, share)

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
        """Compute a level from features submitted before this call.

        仅按 secret 标识引用已存储的 shares；缺少输入时由 party 查询显式失败。
        Refer to stored shares by secret id; missing inputs fail at the party.
        """

        if not input_secret_ids or len(input_secret_ids) != len(weights):
            raise ValueError("input_secret_ids and weights must have the same nonzero length")
        session = self.sessions[session_id]
        if (session.threshold, session.num_parties, session.modulus) != (
            threshold, num_parties, modulus
        ):
            raise ValueError("sharing parameters do not match the session")
        score_shares = session.compute_weighted_sum(
            tuple(input_secret_ids), tuple(weights), "risk_score"
        )
        return self.classify_level_shares(
            score_shares,
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )

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
        """Return output shares via party-local linear computation.

        通过 party-local 线性计算返回 output shares。
        Return output shares through party-local linear computation.
        """

        validate_values_and_weights(values, weights)

        session = ProtocolSession(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )
        self.sessions[session_id] = session

        secret_ids = tuple(f"input_{index}" for index in range(len(values)))
        for secret_id, value in zip(secret_ids, values, strict=True):
            session.submit_secret(secret_id, value)

        return session.compute_weighted_sum(
            secret_ids,
            tuple(weights),
            output_secret_id="weighted_sum",
        )

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
        """Return level-code shares.

        返回等级编码 shares。
        Return shares of the level code.
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

        session_id = f"classification_{len(self.sessions) + 1}"
        session = ProtocolSession(
            session_id=session_id,
            threshold=threshold,
            num_parties=num_parties,
            modulus=modulus,
        )
        self.sessions[session_id] = session

        for share in score_shares:
            session.submit_share("risk_score", share)

        coefficients = self._lookup_polynomial_coefficients(
            thresholds=thresholds,
            level_codes=level_codes,
            score_range=score_range,
            modulus=modulus,
            default_level=default_level,
        )
        return session.evaluate_public_polynomial(
            "risk_score",
            coefficients,
            "risk_level",
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

    def _lookup_polynomial_coefficients(
        self,
        *,
        thresholds: Mapping[str, int],
        level_codes: Mapping[str, int],
        score_range: tuple[int, int],
        modulus: int,
        default_level: str,
    ) -> tuple[int, ...]:
        """Return cached public lookup polynomial coefficients.

        返回缓存的公开查表多项式系数。
        Return cached coefficients for the public lookup polynomial.
        """

        cache_key = (
            tuple(sorted(thresholds.items())),
            tuple(sorted(level_codes.items())),
            score_range,
            modulus,
            default_level,
        )
        if cache_key not in self._classification_coefficients:
            table = threshold_classification_table(
                thresholds=thresholds,
                level_codes=level_codes,
                score_range=score_range,
                default_level=default_level,
            )
            coefficients = interpolate_coefficients(table, modulus)
            self._classification_coefficients[cache_key] = tuple(
                coefficient.value for coefficient in coefficients
            )
        return self._classification_coefficients[cache_key]
