from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from mini_mpc.applications.domain import AuditTask, InsurerInpatientRecord
from mini_mpc.applications.features import FEATURE_VALUE_RANGES
from mini_mpc.applications.policies import AUDIT_WINDOW_START_HOUR, audit_window_start
from mini_mpc.applications.preprocessing import (
    is_eligible_for_audit,
    select_audit_candidates,
)
from mini_mpc.protocol.classification import (
    classify_threshold_level, validate_threshold_classifier,
)
from mini_mpc.runtime.backend import MPCBackend
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy


RISK_LEVEL_CODES = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}
RISK_LEVELS_BY_CODE = {code: level for level, code in RISK_LEVEL_CODES.items()}


@dataclass(frozen=True)
class RiskModelConfig:
    """Public feature order, non-negative weights and risk thresholds.

    特征顺序、非负公开权重及风险阈值属于应用配置，不改变通用协议。
    """

    feature_names: tuple[str, ...]
    weights: tuple[int, ...]
    thresholds: Mapping[str, int]

    def __post_init__(self) -> None:
        """Validate the feature mapping and declared classification range.

        校验特征与权重对应关系，以及分类表覆盖的业务值域。
        """

        if not self.feature_names:
            raise ValueError("feature_names must not be empty")
        if len(self.feature_names) != len(self.weights):
            raise ValueError("feature_names and weights must have the same length")

        for weight in self.weights:
            # 布尔或浮点权重会改变模型含义，入口只接受整数。
            # Accept integer weights only; booleans and floats change model meaning.
            if type(weight) is not int:
                raise TypeError("weights must be integers")
            # 查表值域从 0 开始；业务权重必须非负，通用协议仍允许负权重。
            # The business lookup starts at zero; generic protocols still allow negative weights.
            if weight < 0:
                raise ValueError("risk model weights must be non-negative")

        for level in ("high", "medium", "low"):
            # 三个等级边界必须完整且使用同一整数评分单位。
            # Require all three boundaries in the same integer score units.
            if level not in self.thresholds:
                raise ValueError(f"missing threshold for {level}")
            if type(self.thresholds[level]) is not int:
                raise TypeError(f"threshold for {level} must be an integer")

        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature_names must be distinct")
        if any(name not in FEATURE_VALUE_RANGES for name in self.feature_names):
            raise ValueError("unknown feature name")
        if not self.thresholds["high"] > self.thresholds["medium"] > self.thresholds["low"]:
            raise ValueError("risk thresholds must satisfy high > medium > low")
        validate_threshold_classifier(
            thresholds=self.thresholds, level_codes=RISK_LEVEL_CODES,
            score_range=(0, _maximum_risk_score(self)), default_level="none",
        )


@dataclass(frozen=True)
class InsuranceAuditResult:
    """Risk-level result disclosed to the insurer in the current application API.

    当前应用 API 向医保方返回的风险等级结果。
    Risk-level result returned to the insurer by the current application API.

    当前实现只在接收者侧重构等级编码，不重构原始风险分数。
    The current implementation reconstructs only the level code at the receiver,
    not the raw risk score.
    """

    audit_id: str
    audit_case_id: str
    risk_level: str



def _maximum_risk_score(model: RiskModelConfig) -> int:
    """Return the upper score bound for non-negative business weights.

    根据非负业务权重返回分数上界。
    """
    return sum(
        weight * FEATURE_VALUE_RANGES[name][1]
        for name, weight in zip(model.feature_names, model.weights, strict=True)
    )


DEFAULT_RISK_MODEL = RiskModelConfig(
    feature_names=(
        "telco_absence_hours",
        "telco_max_absence_streak",
        "clinical_need_score",
    ),
    weights=(6, 4, 3),
    thresholds={
        "high": 80,
        "medium": 45,
        "low": 20,
    },
)

# 公开模型版本随权重或阈值变化而更新，供远程任务登记使用。
# Change this public model version when weights or thresholds change.
AUDIT_MODEL_VERSION = "insurance-audit-v1"


def create_audit_task_from_record(
    record: InsurerInpatientRecord,
    *,
    audit_id: str,
    audit_case_id: str,
    patient_link_key: str,
    audit_date: date,
) -> AuditTask:
    """Create an MPC audit task from a prefiltered insurer-side record.

    从已通过医保本地前置筛选的住院记录创建 MPC 稽核任务。
    Create an MPC audit task from an insurer-side inpatient record that has
    already passed local prefiltering.

    任务编号和稽核 case 编号来自任务管理侧，住院记录只提供目标医院等路由信息。
    The audit id and audit case id come from task management; the inpatient
    record only supplies routing information such as the target hospital.
    """

    if not is_eligible_for_audit(record, audit_date):
        raise ValueError("record is not eligible for audit")

    return AuditTask(
        audit_id=audit_id,
        audit_case_id=audit_case_id,
        patient_link_key=patient_link_key,
        hospital_id=record.hospital_id,
        audit_date=audit_date,
    )


def compute_insurance_audit_result(
    task: AuditTask,
    *,
    model: RiskModelConfig = DEFAULT_RISK_MODEL,
    threshold: int = 2,
    num_parties: int = 3,
    modulus: int = 251,
    receiver_id: str = "insurer",
    backend: MPCBackend,
) -> InsuranceAuditResult:
    """Compute an insurer-only result from features already shared by their owners.

    数据方须先提交各自的特征 shares；本入口只引用特征标识和公开权重。
    Data holders submit feature shares first. This entry references their
    secret ids and public weights, then discloses only the level to insurer.
    """

    _validate_modulus_prevents_risk_score_wraparound(
        model=model,
        modulus=modulus,
    )
    if backend is None:
        raise TypeError("backend is required for submitted feature shares")
    if receiver_id != "insurer":
        raise PermissionError("only insurer may receive the audit result")
    for feature_name in model.feature_names:
        if feature_name not in FEATURE_VALUE_RANGES:
            raise ValueError(f"unknown feature name: {feature_name}")

    # 特征顺序也是各方提交时使用的 secret id，不能在这里重新收集明文特征。
    # Feature names are the submitted secret ids; do not collect plaintext here.
    level_shares = backend.compute_submitted_level_shares(
        task.audit_id,
        model.feature_names,
        model.weights,
        thresholds=model.thresholds,
        level_codes=RISK_LEVEL_CODES,
        score_range=(0, _maximum_risk_score(model)),
        threshold=threshold,
        num_parties=num_parties,
        modulus=modulus,
    )

    # 披露层只交付等级 shares。
    # The disclosure layer delivers only level shares.
    disclosure_policy = OutputDisclosurePolicy(
        receiver_id=receiver_id,
        min_shares=threshold,
    )

    # 医保方只重构等级编码。
    # The insurer reconstructs only the level code.
    risk_level_code = backend.disclose_to_receiver(
        level_shares,
        disclosure_policy,
        receiver_id,
    )
    risk_level = _risk_level_from_code(risk_level_code.value)

    return InsuranceAuditResult(
        audit_id=task.audit_id,
        audit_case_id=task.audit_case_id,
        risk_level=risk_level,
    )


def classify_risk_score(score: int, thresholds: Mapping[str, int]) -> str:
    """Map a reconstructed risk score to a risk level.

    将风险分数映射为风险等级。
    Map a risk score to a risk level.
    """

    return classify_threshold_level(
        score,
        thresholds=thresholds,
        default_level="none",
    )


def _validate_modulus_prevents_risk_score_wraparound(
    *,
    model: RiskModelConfig,
    modulus: int,
) -> None:
    """Reject a modulus too small for the configured risk-score range.

    校验应用层风险分数不会发生有限域回绕，避免回绕后的值影响等级分类。
    Check that the application risk score cannot wrap in the finite field and
    affect risk-level classification.
    """

    maximum_score = _maximum_risk_score(model)
    if modulus <= maximum_score:
        raise ValueError("modulus must be greater than maximum risk score")



def _risk_level_from_code(level_code: int) -> str:
    """Decode a disclosed risk-level code.

    解码已披露的风险等级编码。
    """

    if level_code not in RISK_LEVELS_BY_CODE:
        raise ValueError("unknown risk level code")
    return RISK_LEVELS_BY_CODE[level_code]
