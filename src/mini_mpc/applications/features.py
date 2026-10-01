from collections.abc import Mapping, Sequence
from dataclasses import dataclass


FEATURE_VALUE_RANGES = {
    "telco_absence_hours": (0, 10),
    "telco_max_absence_streak": (0, 10),
    "clinical_need_score": (0, 10),
}


def validate_feature_value(feature_name: str, value: int) -> None:
    """Validate a private feature before its owner creates Shamir shares.

    数据方必须在本地校验特征值域，因为业务入口不再接收明文。
    The data holder checks the range locally because the business entry no
    longer receives the plaintext feature.
    """

    if feature_name not in FEATURE_VALUE_RANGES:
        raise ValueError(f"unknown feature name: {feature_name}")
    if type(value) is not int:
        raise TypeError(f"feature must be an integer: {feature_name}")
    minimum, maximum = FEATURE_VALUE_RANGES[feature_name]
    if not minimum <= value <= maximum:
        raise ValueError(f"feature value out of range: {feature_name}")


@dataclass(frozen=True)
class TelcoPresenceFeatures:
    """Telecom-derived absence features for one overnight audit window.

    运营商侧从小时级在院序列中提取的 MPC 输入特征。
    Telecom-side MPC input features derived from an hourly presence sequence.

    这些字段是小范围整数，可以进入 MPC；原始位置事件、nCGI 明细和时间戳不进入 MPC。
    These fields are small integers suitable for MPC; raw location events,
    nCGI details, and timestamps do not enter MPC.
    """

    telco_absence_hours: int
    telco_max_absence_streak: int


def derive_telco_presence_features(
    hourly_presence: Sequence[int],
) -> TelcoPresenceFeatures:
    """Derive telecom MPC features from a normalized hourly presence sequence.

    输入是运营商本地预处理后的小时级在院序列，不是原始位置记录。
    The input is the telecom provider's locally normalized hourly presence
    sequence, not raw location records.

    每个元素必须是 0 或 1；1 表示该小时在目标医院覆盖范围内，0 表示该小时未确认在院。
    Each element must be 0 or 1; 1 means the subscriber is observed near the
    target hospital during that hour, and 0 means presence is not confirmed.
    """

    if len(hourly_presence) != 10:
        raise ValueError("hourly_presence must contain 10 hourly flags")

    for flag in hourly_presence:
        # 逐个检查小时标记，保证后续 count 和连续缺席计算只处理 bit 值。
        # Check each hourly flag so count and absence-streak logic only process bit values.
        if flag not in (0, 1):
            raise ValueError("hourly_presence flags must be 0 or 1")

    # 缺席小时数是所有 0 标记的数量，用来衡量整晚不在院的总时长。
    # Absence hours count all zero flags and measure total non-presence duration.
    absence_hours = hourly_presence.count(0)

    # 统计最长连续未确认在院小时数，不把缺少位置记录视为离院证明。
    # Count consecutive unconfirmed hours; missing events do not prove absence.
    max_absence_streak = _max_zero_streak(hourly_presence)

    return TelcoPresenceFeatures(
        telco_absence_hours=absence_hours,
        telco_max_absence_streak=max_absence_streak,
    )


def derive_clinical_need_score(
    disease_category: str,
    severity_level: str,
    score_table: Mapping[tuple[str, str], int],
) -> int:
    """Derive a hospital-side clinical-necessity risk score.

    医院侧在本地根据疾病类别和严重程度映射出临床必要性不足风险分。
    The hospital locally maps disease category and severity level to a risk
    score for weak clinical support for inpatient care.

    分数表由应用层维护，便于后续根据测试结果调整临床规则。
    The score table is maintained by the application layer so clinical rules can
    be adjusted after evaluation.
    """

    # 用疾病类别和严重程度组成规则表 key，使临床规则集中在可调整的 score_table 中。
    # Use disease category and severity as the rule-table key so clinical rules stay in the adjustable score_table.
    score_key = (disease_category, severity_level)
    if score_key not in score_table:
        raise ValueError("unknown disease category and severity combination")

    # 读取医院本地规则产出的风险分，并在进入 MPC 前限制为小范围整数。
    # Read the locally configured clinical risk score and constrain it to a
    # small integer before MPC.
    score = score_table[score_key]
    if type(score) is not int:
        raise TypeError("clinical need score must be an integer")
    if not 0 <= score <= 10:
        raise ValueError("clinical need score must be between 0 and 10")

    return score


def _max_zero_streak(values: Sequence[int]) -> int:
    """Return the longest consecutive run of zeroes.

    返回序列中最长连续 0 的长度。
    Return the length of the longest consecutive zero run.
    """

    longest = 0
    current = 0
    for value in values:
        # 遍历每个小时标记：遇到 0 就延长当前缺席段，遇到 1 就重置当前缺席段。
        # Iterate over hourly flags: zero extends the current absence run, one resets it.
        if value == 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
