# Mini MPC

[English](#english)

基于 Shamir/BGW 的医保住院风险协同计算原型。医保发起稽核；运营商和医院在本地提取特征并提交秘密份额；三个独立 HTTPS 计算节点完成评分和分类；医保只获得最终风险等级。

数据为合成样本。风险等级用于人工核查，不能直接认定骗保。当前采用半诚实模型，尚无生产部署或真实业务效果验证。

## 快速开始

需要 Python 3.11+。完整网络测试还需要 OpenSSL；MySQL 流程需要 Docker Compose。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mysql]'
.venv/bin/python examples/insurance_audit.py
.venv/bin/python -m pytest
```

示例使用内存 backend，输出 `risk_level=medium`。未设置 MySQL 测试地址时，9 个集成用例跳过。独立进程、证书和数据库步骤见 [运行文档](docs/running.md)。

## 业务与模型

审计窗口为当日 22:00 至次日 08:00。医保只选取仍在住院、且入院时间不晚于窗口开始的病例。

| 特征 | 来源 | 含义 | 公开权重 |
| --- | --- | --- | --- |
| `telco_absence_hours` | 运营商 | 未确认在院的小时数 | 6 |
| `telco_max_absence_streak` | 运营商 | 最长连续未确认在院小时数 | 4 |
| `clinical_need_score` | 医院 | 临床必要性不足风险分，越高越不支持住院 | 3 |

特征均为 0–10 的整数，业务权重须为非负整数；默认加权分数为 0–130。阈值 20、45、80 对应 none、low、medium、high。分类在共享分数上完成，不重构分数。权重和阈值为未校准规则。

## 已实现能力

- 素数域、多项式插值、Shamir 分享、BGW 乘法降阶和共享值上的等级查表。
- `MPCBackend` 接口及内存、远程两个实现；业务入口只引用已提交份额。
- 三个机构各用本方 view-only MySQL 账号读取数据；原始记录留在本地。
- 三个独立 party 进程、mTLS 身份、有限期任务登记、角色授权和固定计算计划。
- 冲突写入及越序拒绝、精确重试幂等回执、BGW 原份额续传和白名单安全事件。
- 最终等级仅交付登记的医保接收者；正式服务拒绝任意中间份额读取。

## 安全边界

默认三个 party、重构门限二、模数 251。单个 party 不能恢复秘密；两个 party 合谋可以重构。等级仍泄露区间信息。内存 backend 在同一进程中模拟 parties。

状态只存于内存，不支持跨重启恢复。当前没有恶意参与方安全、特征真实性证明、PSI、完整查询预算或生产密钥治理。本地多进程不等于跨机构部署。完整说明见 [威胁模型](docs/threat_model.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [系统设计](docs/design.md) | 角色、数据流、模块与设计取舍 |
| [计算协议](docs/protocol.md) | 参数、Shamir、BGW 和等级查表 |
| [威胁模型](docs/threat_model.md) | 信任、权限、披露与限制 |
| [本地运行](docs/running.md) | 安装、数据库、证书及完整流程 |
| [测试验收](docs/testing.md) | 命令、覆盖及既有验收记录 |

## 目录

```text
src/mini_mpc/   应用、runtime、网络、协议、分享及数学模块
configs/       部署配置模板
examples/      可运行的内存示例
tests/         单元、网络、CLI 和 MySQL 集成测试
docker/        测试数据库初始化 SQL
docs/          项目文档
```

依赖与打包配置见 `pyproject.toml`。许可证：[MIT](LICENSE)。

---

# English

Mini MPC is a Shamir/BGW prototype for joint inpatient insurance risk assessment. The insurer starts an audit. Telecom and hospital nodes derive features locally and submit secret shares. Three independent HTTPS compute nodes score and classify them. Only the insurer receives the final risk level.

Data is synthetic. Levels guide manual review; they do not establish fraud. The model is semi-honest, without production deployment or validated business accuracy.

## Quick start

Requires Python 3.11+. Full network tests also need OpenSSL; the MySQL workflow needs Docker Compose.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mysql]'
.venv/bin/python examples/insurance_audit.py
.venv/bin/python -m pytest
```

The memory example outputs `risk_level=medium`. Nine integration cases skip without a MySQL test address. See [running](docs/running.md#english) for processes, certificates and database setup.

## Business model

The audit window is 22:00 to 08:00 the next day. The insurer selects current inpatients admitted no later than the window start.

| Feature | Owner | Meaning | Public weight |
| --- | --- | --- | --- |
| `telco_absence_hours` | Telecom | Hours with no confirmed hospital presence | 6 |
| `telco_max_absence_streak` | Telecom | Longest consecutive absence streak | 4 |
| `clinical_need_score` | Hospital | Higher values mean weaker clinical support for inpatient care | 3 |

Features are integers in 0–10; business weights must be non-negative integers. Default scores range from 0 to 130. Thresholds 20, 45 and 80 define none, low, medium and high. Classification runs on the shared score without revealing it. Weights and thresholds are uncalibrated rules.

## Implemented

- Prime fields, polynomial interpolation, Shamir sharing, BGW degree reduction and level lookup on shared values.
- `MPCBackend` with memory and remote implementations; the business entry references submitted shares only.
- Separate view-only MySQL accounts for each organization; raw records remain local.
- Three party processes, mTLS identities, finite-lived registration, role authorization and a fixed computation plan.
- Conflict and order checks, idempotent exact retries, original-share BGW retries and allowlisted security events.
- Final levels delivered only to the registered insurer; arbitrary intermediate-share reads are rejected.

## Security boundaries

Defaults are three parties, threshold two and modulus 251. One party cannot recover secrets; two colluding parties can. Levels still reveal interval information. The memory backend simulates parties in one process.

State is in memory, without restart recovery. Malicious security, feature-truth proofs, PSI, full query budgets and production key governance are absent. Local processes do not imply deployment across organizations. See the [threat model](docs/threat_model.md#english).

## Documentation

| Document | Content |
| --- | --- |
| [System design](docs/design.md#english) | Roles, data flow, modules and choices |
| [Protocol](docs/protocol.md#english) | Parameters, Shamir, BGW and level lookup |
| [Threat model](docs/threat_model.md#english) | Trust, authorization, disclosure and limits |
| [Local running](docs/running.md#english) | Setup, database, certificates and workflow |
| [Testing](docs/testing.md#english) | Commands, coverage and recorded validation |

## Layout

```text
src/mini_mpc/   Application, runtime, network, protocol, sharing and math
configs/       Deployment templates
examples/      Runnable memory example
tests/         Unit, network, CLI and MySQL integration tests
docker/        Test database initialization SQL
docs/          Project documentation
```

Dependencies and packaging are in `pyproject.toml`. License: [MIT](LICENSE).
