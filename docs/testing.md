# 测试与验收

[English](#english)

测试覆盖数学正确性、业务数据流和正式服务授权，不构成生产安全证明。环境安装见 [running.md](running.md)。

## 运行

普通测试需要 Python 3.11+、pytest 和 OpenSSL，不依赖 Docker：

```bash
.venv/bin/python -m pytest
```

完整 MySQL 验证需先启动并确认 [测试数据库](running.md#mysql-测试环境) 为 healthy，再执行：

```bash
MINI_MPC_MYSQL_HOST=127.0.0.1 .venv/bin/python -m pytest
```

验证发布前输入、HTTP 和授权边界：

```bash
.venv/bin/python -m pytest tests/test_field.py tests/test_insurance_audit.py tests/test_preprocessing.py tests/test_party_service_config.py tests/test_http_limits.py tests/test_service_authorization.py
```

只验证正式授权、CLI 和会话流程：

```bash
.venv/bin/python -m pytest tests/test_service_authorization.py tests/test_audit_cli.py tests/test_audit_execution.py
```

未设置 `MINI_MPC_MYSQL_HOST` 时，9 个 MySQL 用例跳过。默认使用初始化的三个 view-only 账号，可通过 `MINI_MPC_<ROLE>_MYSQL_USER`、`PASSWORD` 覆盖；共享测试数据库地址使用 `MINI_MPC_MYSQL_HOST`、`PORT`。OpenSSL 不可用时，动态 PKI 用例会跳过。

## 测试边界

- `RunningAuditClients` 复用正式服务构造路径，使用六个独立证书身份；业务测试先登记任务，再按机构提交。共用服务但使用独立会话隔离状态。
- `RunningPartyClients` 显式启用无客户端身份的协议对照模式，允许任意算术与内部份额检查，不能作为正式业务验收。
- CLI 普通测试只替换数据库读取，配置加载、reader、预处理、mTLS 与计算仍运行实际代码。真实 GRANT 和 view 查询由 MySQL 用例验证。
- 正式与协议对照夹具共用动态 PKI helper；证书在临时目录签发并清理，源码不含固定私钥。

## 关键覆盖

| 范围 | 主要用例 | 验证内容 |
| --- | --- | --- |
| 数学与分享 | `test_field`、`test_polynomial`、`test_share`、`test_shamir` | 域运算、插值、门限、兼容性和非法输入 |
| 协议与 backend | `test_protocols`、`test_party_session`、`test_backend`、`test_disclosure` | 评分、BGW、等级分类、party 隔离及指定接收者 |
| 本地业务 | `test_insurance_audit`、`test_preprocessing`、`test_view_adapters`、`test_view_readers` | 在院筛选、窗口、映射有效性、临床规则和本方账号 |
| HTTP 资源边界 | `test_http_limits` | 请求长度、64 KiB 边界、读取 / TLS 握手超时、提前 EOF 及拒绝后状态不变 |
| 配置与请求授权 | `test_party_service_config`、`test_authorization`、`test_task_registry`、`test_service_authorization` | 无证书 / 未知身份、角色冒用、模型 / 权重 / peer 篡改、错误份额、任意读取及过期拒绝 |
| 正式计算与恢复 | `test_network_nodes`、`test_remote_backend`、`test_audit_execution`、`test_session_execution` | 四等级、仅交付等级、越序、缺输入、会话隔离、并发幂等与原份额续传 |
| CLI 与独立进程 | `test_audit_cli`、`integration/test_mysql_views` | 四阶段流程、真实 view-only 权限、三个独立 party、重复调度及代表性失败 |

拒绝矩阵检查 HTTP 状态、错误类别、份额 / 清单 / 有效进度不变，以及白名单安全事件。完整业务测试禁止医保调用任意 `get_share`；测试代码可在服务内部重构分数作为数学对照。独立进程测试检查实际 stderr 事件及失败时没有成功输出。

## 验收快照

修复主体通过真实 MySQL 全量测试：`301 passed`，耗时约 6 分 55 秒。随后补充份额入口校验顺序，相关六个模块定向测试为 `123 passed`，耗时 21.87 秒。

默认验证参数为三个 party、门限二、模数 251。测试不验证真实业务效果、跨机构主机隔离、跨重启恢复、malicious security、生产密钥治理或拒绝服务防护；完整边界见 [threat_model.md](threat_model.md)。

---

# English

Tests cover mathematical correctness, business data flow and service authorization. They do not prove production security. Setup is in [running](running.md#english).

## Commands

Ordinary tests need Python 3.11+, pytest and OpenSSL, without Docker:

```bash
.venv/bin/python -m pytest
```

For full MySQL validation, start the [test database](running.md#mysql-test-environment), wait for `healthy`, then run:

```bash
MINI_MPC_MYSQL_HOST=127.0.0.1 .venv/bin/python -m pytest
```

For release input, HTTP and authorization boundaries:

```bash
.venv/bin/python -m pytest tests/test_field.py tests/test_insurance_audit.py tests/test_preprocessing.py tests/test_party_service_config.py tests/test_http_limits.py tests/test_service_authorization.py
```

For service authorization, CLI and session flow only:

```bash
.venv/bin/python -m pytest tests/test_service_authorization.py tests/test_audit_cli.py tests/test_audit_execution.py
```

Without `MINI_MPC_MYSQL_HOST`, nine MySQL cases skip. Tests use the three initialized view-only accounts. Override credentials with `MINI_MPC_<ROLE>_MYSQL_USER` and `PASSWORD`; use `MINI_MPC_MYSQL_HOST` and `PORT` for the shared test address. Dynamic PKI tests skip when OpenSSL is absent.

## Test boundaries

- `RunningAuditClients` uses the service construction path and six separate certificate identities. Business tests register tasks before submitting inputs. Shared services keep sessions isolated.
- `RunningPartyClients` explicitly enables protocol reference mode without client identity checks. It allows arbitrary arithmetic and internal-share inspection; it is not business authorization evidence.
- Ordinary CLI tests replace only database reads. Configuration, readers, preprocessing, mTLS and computation use actual code. MySQL tests verify real grants and view queries.
- Service and protocol reference fixtures share the dynamic PKI helper. Certificates are issued and cleaned in temporary directories; source code contains no fixed private keys.

## Main coverage

| Scope | Test modules | Checks |
| --- | --- | --- |
| Math and sharing | `test_field`, `test_polynomial`, `test_share`, `test_shamir` | Arithmetic, interpolation, threshold, compatibility and invalid inputs |
| Protocol and backend | `test_protocols`, `test_party_session`, `test_backend`, `test_disclosure` | Scoring, BGW, classification, party isolation and receiver restriction |
| Local business logic | `test_insurance_audit`, `test_preprocessing`, `test_view_adapters`, `test_view_readers` | Stay filtering, windows, map validity, clinical rules and local credentials |
| HTTP limits | `test_http_limits` | Body framing, 64 KiB boundary, read/TLS timeouts, early EOF and unchanged state after rejection |
| Configuration and authorization | `test_party_service_config`, `test_authorization`, `test_task_registry`, `test_service_authorization` | Missing certificate, unknown identity, role spoofing, model/weight/peer changes, wrong shares, arbitrary reads and expiry |
| Computation and retries | `test_network_nodes`, `test_remote_backend`, `test_audit_execution`, `test_session_execution` | Four levels, level-only output, wrong order, missing input, isolation, concurrency and original-share retries |
| CLI and independent processes | `test_audit_cli`, `integration/test_mysql_views` | Four stages, real view grants, three party processes, repeated scheduling and representative failures |

The rejection matrix checks HTTP status, error category, unchanged shares/manifests/valid progress, and allowlisted events. Full business tests forbid insurer calls to arbitrary `get_share`. Tests may reconstruct scores inside services as a mathematical reference. Process tests check actual stderr events and no success output on failure.

## Validation snapshot

The main fixes passed the full suite with real MySQL: `301 passed` in about 6 minutes 55 seconds. After adjusting share validation order, targeted tests across six modules returned `123 passed` in 21.87 seconds.

Defaults are three parties, threshold two and modulus 251. Tests do not validate business accuracy, host isolation, restart recovery, malicious security, production key governance or denial-of-service protection. See the [threat model](threat_model.md#english).
