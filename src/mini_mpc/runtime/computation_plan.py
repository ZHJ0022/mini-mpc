"""Public Horner operations shared by orchestration and party authorization.

公开的 Horner 指令由调度方和 party 共同使用，避免两边维护不同的步骤。
The coordinator and parties share these public Horner steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mini_mpc.network.task_registry import TaskManifest


@dataclass(frozen=True)
class ComputationStep:
    """One public operation; values of private shares are never included.

    单个公开操作；不包含任何私有 share 的值。
    """

    kind: Literal["constant", "sum", "multiply"]
    # output_id 是步骤写入的 share 标识；input_ids 保持协议规定的顺序。
    # output_id names the written share; input_ids preserve protocol order.
    output_id: str
    input_ids: tuple[str, ...] = ()
    # weights 只用于公开线性组合；value 只用于公开常量步骤。
    # weights belong to public sums; value belongs to public constants.
    weights: tuple[int, ...] = ()
    value: int | None = None


@dataclass(frozen=True)
class AuditComputationPlan:
    """Server-owned public model and exact allowed operations.

    服务端持有的公开模型及允许的精确操作；请求不能改变这些参数。
    Server-owned public model and exact operations; requests cannot alter them.
    """

    model_version: str
    feature_ids: tuple[str, ...]
    # 固定的分享参数与任务清单逐项比对，防止同版本任务改变计算域。
    # Fixed sharing parameters bind the manifest to this deployed model.
    threshold: int
    num_parties: int
    modulus: int
    steps: tuple[ComputationStep, ...]

    def validate_manifest(self, manifest: TaskManifest) -> None:
        """Bind a registered task to the deployed model and sharing parameters.

        将注册任务绑定到部署模型与分享参数，拒绝请求方另选模型。
        Bind the registered task to the deployed model and sharing parameters.
        """

        if (
            manifest.model_version != self.model_version
            or manifest.feature_secret_ids != self.feature_ids
            or manifest.threshold != self.threshold
            or manifest.num_parties != self.num_parties
            or manifest.modulus != self.modulus
        ):
            raise PermissionError("task does not match the configured audit model")

    def require_step(self, step: ComputationStep) -> int:
        """Allow only an exact public instruction from the plan.

        只允许计划中的精确公开指令；权重、标识或常量变化均拒绝。
        Allow only an exact planned instruction, including ids and public values.
        """

        # 返回同一指令表中的位置，供会话执行顺序检查复用。
        # Return the position in the same instruction table for execution-order checks.
        try:
            return self.steps.index(step)
        except ValueError as exc:
            raise PermissionError("operation is not in the configured audit plan") from exc

    def require_reshare(self, secret_id: str, sender_id: int) -> ComputationStep:
        """Bind a party's reshare id to a planned multiplication output.

        将 party 的 reshare 标识绑定到计划中的乘法输出。
        Bind a party reshare id to a planned multiplication output.
        """

        for step in self.steps:
            if (step.kind == "multiply"
                and secret_id == f"{step.output_id}__bgw_reshare_from_{sender_id}"):
                return step
        raise PermissionError("reshare is not in the configured audit plan")


def polynomial_steps(
    value_secret_id: str,
    coefficients: tuple[int, ...],
    output_secret_id: str,
    num_parties: int,
) -> tuple[ComputationStep, ...]:
    """Describe the existing Horner and BGW reduction sequence.

    描述现有 Horner 求值及 BGW 降阶步骤，供执行和服务端校验复用。
    Describe the existing Horner evaluation and BGW reduction for both uses.
    """

    if not coefficients:
        raise ValueError("coefficients must not be empty")
    if len(coefficients) == 1:
        return (ComputationStep("constant", output_secret_id, value=coefficients[0]),)

    steps = []
    # accumulator_id 是 Horner 当前累计值；每轮用输入值乘它再加一项常量。
    # accumulator_id is the current Horner value, multiplied and incremented each round.
    accumulator_id = f"{output_secret_id}_constant_{len(coefficients) - 1}"
    steps.append(ComputationStep("constant", accumulator_id, value=coefficients[-1]))
    for index in range(len(coefficients) - 2, -1, -1):
        # product_id 与 constant_id 分别保存本轮乘积和公开系数。
        # product_id and constant_id hold this round's product and public coefficient.
        product_id = f"{output_secret_id}_product_{index}"
        constant_id = f"{output_secret_id}_constant_{index}"
        next_id = output_secret_id if index == 0 else f"{output_secret_id}_step_{index}"
        steps.append(ComputationStep(
            "multiply", product_id, (accumulator_id, value_secret_id)
        ))
        # 发送方已经乘过 Lagrange 系数，接收方只需相加。
        # Senders applied Lagrange coefficients; receivers only add reshares.
        reshare_ids = tuple(
            f"{product_id}__bgw_reshare_from_{party_id}"
            for party_id in range(1, num_parties + 1)
        )
        steps.append(ComputationStep(
            "sum", product_id, reshare_ids, (1,) * num_parties
        ))
        steps.append(ComputationStep("constant", constant_id, value=coefficients[index]))
        steps.append(ComputationStep(
            "sum", next_id, (product_id, constant_id), (1, 1)
        ))
        accumulator_id = next_id
    return tuple(steps)
