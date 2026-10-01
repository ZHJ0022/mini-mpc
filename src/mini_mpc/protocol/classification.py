from collections.abc import Mapping


def validate_threshold_classifier(
    *,
    thresholds: Mapping[str, int],
    level_codes: Mapping[str, int],
    score_range: tuple[int, int],
    default_level: str,
) -> None:
    """Validate threshold-based risk-level configuration.

    校验基于阈值的风险等级配置。
    """

    if not thresholds:
        raise ValueError("thresholds must not be empty")
    if default_level not in level_codes:
        raise ValueError("level_codes must contain the default level")

    if len(score_range) != 2:
        raise ValueError("score_range must contain minimum and maximum scores")
    minimum_score, maximum_score = score_range
    if type(minimum_score) is not int or type(maximum_score) is not int:
        raise TypeError("score_range values must be integers")
    if minimum_score > maximum_score:
        raise ValueError("score_range minimum must not exceed maximum")

    # 先校验类型再排序，避免混合类型在排序阶段产生无关异常。
    # Validate types before sorting so mixed input types fail at the boundary.
    if any(type(boundary) is not int for boundary in thresholds.values()):
        raise TypeError("threshold values must be integers")
    previous_boundary = maximum_score + 1
    for level, boundary in sorted(
        thresholds.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        if level not in level_codes:
            raise ValueError(f"missing level code for {level}")
        if boundary < minimum_score or boundary > maximum_score:
            raise ValueError("threshold values must be inside score_range")
        if boundary >= previous_boundary:
            raise ValueError("threshold values must be distinct")
        previous_boundary = boundary

    for level_code in level_codes.values():
        if type(level_code) is not int:
            raise TypeError("level codes must be integers")
        if level_code < 0:
            raise ValueError("level codes must be non-negative integers")


def classify_threshold_level(
    score: int,
    *,
    thresholds: Mapping[str, int],
    default_level: str,
) -> str:
    """Classify a plaintext score with descending threshold boundaries.

    按降序阈值对明文分数分类。
    Classify a plaintext score using descending thresholds.
    """

    for level, boundary in sorted(
        thresholds.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        if score >= boundary:
            return level
    return default_level


def threshold_classification_table(
    *,
    thresholds: Mapping[str, int],
    level_codes: Mapping[str, int],
    score_range: tuple[int, int],
    default_level: str,
) -> tuple[tuple[int, int], ...]:
    """Build public score-to-level points for secure lookup.

    生成公开的分数到等级编码映射点，用于安全查表。
    Build public score-to-level-code points for secure lookup.
    """

    validate_threshold_classifier(
        thresholds=thresholds,
        level_codes=level_codes,
        score_range=score_range,
        default_level=default_level,
    )

    minimum_score, maximum_score = score_range
    points = []
    for score in range(minimum_score, maximum_score + 1):
        level = classify_threshold_level(
            score,
            thresholds=thresholds,
            default_level=default_level,
        )
        points.append((score, level_codes[level]))
    return tuple(points)
