from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from mini_mpc.applications.insurance_audit import (
    AUDIT_MODEL_VERSION, DEFAULT_RISK_MODEL, RISK_LEVEL_CODES, _maximum_risk_score,
)
from mini_mpc.math.polynomial import interpolate_coefficients
from mini_mpc.network.config import PartyServiceConfig, load_party_service_config
from mini_mpc.network.http_server import MPCPartyHTTPServer, make_party_server
from mini_mpc.protocol.classification import threshold_classification_table
from mini_mpc.runtime.computation_plan import (
    AuditComputationPlan, ComputationStep, polynomial_steps,
)


def build_party_server(config: PartyServiceConfig) -> MPCPartyHTTPServer:
    """Build one MPC party server from validated config.

    根据已校验配置构造一个 MPC party 服务。
    """

    # 依据部署的公开模型生成确定的指令表；不能信任医保请求自报的模型步骤。
    # Derive exact operations from the deployed public model, not request data.
    model = DEFAULT_RISK_MODEL
    # table 逐分数给出公开等级编码；coefficients 是其有限域插值多项式。
    # table maps every public score to a level; coefficients interpolate that table in the field.
    table = threshold_classification_table(
        thresholds=model.thresholds,
        level_codes=RISK_LEVEL_CODES,
        score_range=(0, _maximum_risk_score(model)),
        default_level="none",
    )
    coefficients = tuple(
        coefficient.value for coefficient in interpolate_coefficients(table, 251)
    )
    # plan 同时约束任务参数、首次加权求和及每一轮 Horner/BGW 指令。
    # plan binds task parameters, the first weighted sum, and each Horner/BGW instruction.
    plan = AuditComputationPlan(
        model_version=AUDIT_MODEL_VERSION,
        feature_ids=model.feature_names,
        threshold=2,
        num_parties=3,
        modulus=251,
        steps=(
            ComputationStep("sum", "weighted_sum", model.feature_names, model.weights),
            *polynomial_steps("weighted_sum", coefficients, "risk_level", 3),
        ),
    )
    return make_party_server(
        config.party_id,
        host=config.host,
        port=config.port,
        tls_config=config.tls,
        peer_tls_config=config.peer_tls,
        client_fingerprints=config.client_fingerprints,
        peer_endpoints=config.peer_endpoints,
        computation_plan=plan,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one standalone MPC party service.

    运行一个独立 MPC party 服务进程。
    """

    parser = argparse.ArgumentParser(description="Run one Mini MPC party service")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a JSON party-service config file",
    )
    args = parser.parse_args(argv)

    # 独立服务将白名单安全事件以 JSON 行写入 stderr，不混入业务 stdout。
    # Standalone services emit allowlisted security JSON to stderr, separate from business stdout.
    event_logger = logging.getLogger("mini_mpc.security")
    if not event_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        event_logger.addHandler(handler)
    event_logger.setLevel(logging.INFO)
    event_logger.propagate = False

    config = load_party_service_config(args.config)
    server = build_party_server(config)
    host, port = server.server_address

    # 启动日志只包含服务元数据，不能输出 shares、原始字段或证书私钥路径内容。
    # Startup logs include service metadata only; never log shares, raw fields, or private-key contents.
    print(
        f"starting MPC party service party_id={config.party_id} "
        f"endpoint={server.scheme}://{host}:{port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping MPC party service", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
