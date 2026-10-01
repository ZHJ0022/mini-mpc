# 本地运行

[English](#english)

以下命令从项目根目录在 Bash 中执行，使用合成数据、本地 MySQL 和三个独立 HTTPS party 进程。机构命令各自加载本方数据库凭据及 mTLS 证书。

## 安装与快速示例

需要 Python 3.11+、OpenSSL 和 Docker Compose；Docker 仅用于 MySQL 流程。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mysql]'
.venv/bin/python examples/insurance_audit.py
```

示例使用内存 backend，预期打印任务标识及 `risk_level=medium`。正式网络流程如下。

## MySQL 测试环境

```bash
docker compose up -d mysql
docker compose ps
```

确认 MySQL 为 `healthy` 后继续。首次初始化执行 `docker/mysql/init/` 中的五份 SQL，创建三个 schema、view、reader 账号及种子数据：

| 机构 | Schema | 本方可读 view | 测试 reader |
| --- | --- | --- | --- |
| 医保 | `mini_mpc_insurer_test` | `v_insurer_active_inpatients` | `mini_mpc_insurer_reader` |
| 运营商 | `mini_mpc_telco_test` | `v_telco_hourly_presence` | `mini_mpc_telco_reader` |
| 医院 | `mini_mpc_hospital_test` | `v_hospital_clinical_scoring` | `mini_mpc_hospital_reader` |

reader 只获本方 view 的 SELECT 权限，密码与测试账号同名。医保 view 不暴露身份证号；运营商 view 按预置授权请求聚合十小时标记；医院 view 使用本地规则表映射评分。账号、数据和 Compose 密码只用于测试，Compose 仅向本机 `127.0.0.1:3306` 发布数据库端口。

MySQL `DATETIME` 按统一约定的本地无时区时间解释。事件 API 使用带时区输入时，须显式传入审计时区；不得混用带时区和无时区时间。

种子数据覆盖四个等级及晚入院、已出院、映射失效等边界。已有数据目录不会因重复启动重新执行初始化 SQL；本流程不清空已有数据库。

## 生成临时证书与配置

公开 JSON 是模板，必须替换证书路径及指纹。下面复用测试 PKI 函数生成短期 CA、六个身份的证书，并派生机构和 party 配置；私钥与运行配置位于系统临时目录。

```bash
export MPC_RUN_DIR="$(mktemp -d)"
PYTHONPATH=src:tests .venv/bin/python - <<'PY'
import json
import os
from pathlib import Path
from network_test_support import write_test_pki

run_dir = Path(os.environ["MPC_RUN_DIR"]).resolve()
identities = ("insurer", "telco", "hospital", "party-1", "party-2", "party-3")
ca, materials, fingerprints = write_test_pki(run_dir / "certs", identities)
for party_id in (1, 2, 3):
    config = json.loads(Path(f"configs/examples/party{party_id}.json").read_text())
    cert, key = materials[f"party-{party_id}"]
    config["tls"].update(cafile=ca, certfile=cert, keyfile=key)
    config["client_fingerprints"] = fingerprints
    (run_dir / f"party{party_id}.json").write_text(json.dumps(config), encoding="utf-8")
for identity in ("insurer", "telco", "hospital"):
    config = json.loads(Path("configs/examples/remote_client.json").read_text())
    cert, key = materials[identity]
    config["tls"].update(cafile=ca, certfile=cert, keyfile=key)
    (run_dir / f"remote_{identity}.json").write_text(json.dumps(config), encoding="utf-8")
print(run_dir)
PY
```

模板使用 127.0.0.1:8441–8443；这些端口须空闲。若调整地址，须同时修改 party 监听、所有 peer 地址、所有客户端 endpoints 和证书 SAN。正式服务保持证书校验及 `require_client_cert=true`。相对证书路径按 JSON 文件目录解析，上述脚本使用绝对路径。

## 启动并检查 party

在同一 Bash 会话继续：

```bash
MPC_PARTY_PIDS=()
for MPC_PARTY_ID in 1 2 3; do
  .venv/bin/python -m mini_mpc.network.party_service \
    --config "$MPC_RUN_DIR/party$MPC_PARTY_ID.json" \
    > "$MPC_RUN_DIR/party$MPC_PARTY_ID.out" \
    2> "$MPC_RUN_DIR/party$MPC_PARTY_ID.events.jsonl" &
  MPC_PARTY_PIDS+=("$!")
done
```

启动包含公开模型插值。使用医保证书检查三个服务，连接错误最多等待每个节点 60 秒；权限或配置错误直接失败。

```bash
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from time import monotonic, sleep
from mini_mpc.runtime.remote_backend import RemoteShamirBackend

config = Path(os.environ["MPC_RUN_DIR"]) / "remote_insurer.json"
backend = RemoteShamirBackend.from_config(config)
for client in backend.party_clients:
    deadline = monotonic() + 60
    while True:
        try:
            print(client.health())
            break
        except OSError:
            if monotonic() >= deadline:
                raise
            sleep(0.2)
PY
```

`/health` 检查服务存活，不代表任务可执行。启动信息在 `.out`，安全事件在 `.events.jsonl`；错误可先检查对应文件。

## 执行一次稽核

以下 shell 数组仅复用本地测试参数。每个命令进程仍只注入本方凭据；CLI 角色不能覆盖证书身份。默认 MySQL 地址为 127.0.0.1:3306，可用本方 `MINI_MPC_<ROLE>_MYSQL_HOST`、`PORT` 覆盖。

```bash
MPC_AUDIT_ARGS=(--session-id audit-local-001 --patient-link-key patient-link-001 \
  --hospital-id hospital-001 --audit-date 2026-08-28)
MPC_INSURER_ARGS=(--inpatient-record-id insurer-inpatient-001 --audit-case-id audit-case-local-001)
MPC_EXPIRES_AT="$(.venv/bin/python -c 'import time; print(int(time.time()) + 3600)')"

MINI_MPC_INSURER_MYSQL_USER=mini_mpc_insurer_reader \
MINI_MPC_INSURER_MYSQL_PASSWORD=mini_mpc_insurer_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli insurer-register \
  --client-config "$MPC_RUN_DIR/remote_insurer.json" \
  "${MPC_AUDIT_ARGS[@]}" "${MPC_INSURER_ARGS[@]}" --expires-at "$MPC_EXPIRES_AT"

MINI_MPC_TELCO_MYSQL_USER=mini_mpc_telco_reader \
MINI_MPC_TELCO_MYSQL_PASSWORD=mini_mpc_telco_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli telco \
  --client-config "$MPC_RUN_DIR/remote_telco.json" "${MPC_AUDIT_ARGS[@]}"

MINI_MPC_HOSPITAL_MYSQL_USER=mini_mpc_hospital_reader \
MINI_MPC_HOSPITAL_MYSQL_PASSWORD=mini_mpc_hospital_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli hospital \
  --client-config "$MPC_RUN_DIR/remote_hospital.json" "${MPC_AUDIT_ARGS[@]}"

MINI_MPC_INSURER_MYSQL_USER=mini_mpc_insurer_reader \
MINI_MPC_INSURER_MYSQL_PASSWORD=mini_mpc_insurer_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli insurer \
  --client-config "$MPC_RUN_DIR/remote_insurer.json" \
  "${MPC_AUDIT_ARGS[@]}" "${MPC_INSURER_ARGS[@]}"
```

依次预期输出：`status=registered`、两次 `status=submitted`、`risk_level=medium`。分数与特征不出现在业务输出中。运营商授权查询范围由种子请求预置，尚无动态授权请求接入。

## 重试与停止

登记重试须沿用相同清单和截止时间。party 仍运行且任务未过期时，可重新执行医保计算命令：完成步骤只回执，BGW 失败使用原份额续传。运营商 / 医院重跑会重新随机分享，不是相同输入重试；请用新会话执行另一轮完整测试。

同一乘法并发分发返回 `in_progress`；过期任务不能继续执行、领取或延长。party 重启丢失会话状态。详细边界见 [threat_model.md](threat_model.md)。

```bash
kill "${MPC_PARTY_PIDS[@]}"
wait "${MPC_PARTY_PIDS[@]}" || true
docker compose stop mysql
```

临时目录含本次测试证书和日志，停止后可删除该目录。测试命令及覆盖见 [testing.md](testing.md)。

---

# English

Run these commands from the project root in Bash. The workflow uses synthetic data, local MySQL and three independent HTTPS party processes. Each organization command loads only its own database credentials and mTLS certificate.

## Setup and quick example

Requires Python 3.11+, OpenSSL and Docker Compose. Docker is needed only for the MySQL workflow.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mysql]'
.venv/bin/python examples/insurance_audit.py
```

The example uses the memory backend. It prints task IDs and `risk_level=medium`. The network workflow follows.

## MySQL test environment

```bash
docker compose up -d mysql
docker compose ps
```

Wait for MySQL to be `healthy`. On first initialization, five SQL files in `docker/mysql/init/` create three schemas, views, reader accounts and seed data:

| Organization | Schema | Allowed view | Test reader |
| --- | --- | --- | --- |
| Insurer | `mini_mpc_insurer_test` | `v_insurer_active_inpatients` | `mini_mpc_insurer_reader` |
| Telecom | `mini_mpc_telco_test` | `v_telco_hourly_presence` | `mini_mpc_telco_reader` |
| Hospital | `mini_mpc_hospital_test` | `v_hospital_clinical_scoring` | `mini_mpc_hospital_reader` |

Readers get SELECT on their own view only. Test passwords equal usernames. The insurer view excludes identity-card numbers. The telecom view aggregates ten hourly flags for pre-authorized requests. The hospital view maps local scoring rules. Credentials, data and Compose passwords are for tests; Compose publishes the database only on `127.0.0.1:3306`.

MySQL `DATETIME` uses one agreed local time without timezone information. Timezone-aware event inputs require an explicit audit timezone. Do not mix aware and naive timestamps.

Seeds cover four levels, late admission, discharge and expired mappings. Restarting an existing data directory does not rerun initialization SQL. This workflow keeps existing database data.

## Generate temporary certificates and configuration

Public JSON files are templates. Replace certificate paths and fingerprints. This script reuses the test PKI helper to issue a short-lived CA and six node certificates, then writes party and organization configurations. Keys and runtime files stay in a system temporary directory.

```bash
export MPC_RUN_DIR="$(mktemp -d)"
PYTHONPATH=src:tests .venv/bin/python - <<'PY'
import json
import os
from pathlib import Path
from network_test_support import write_test_pki

run_dir = Path(os.environ["MPC_RUN_DIR"]).resolve()
identities = ("insurer", "telco", "hospital", "party-1", "party-2", "party-3")
ca, materials, fingerprints = write_test_pki(run_dir / "certs", identities)
for party_id in (1, 2, 3):
    config = json.loads(Path(f"configs/examples/party{party_id}.json").read_text())
    cert, key = materials[f"party-{party_id}"]
    config["tls"].update(cafile=ca, certfile=cert, keyfile=key)
    config["client_fingerprints"] = fingerprints
    (run_dir / f"party{party_id}.json").write_text(json.dumps(config), encoding="utf-8")
for identity in ("insurer", "telco", "hospital"):
    config = json.loads(Path("configs/examples/remote_client.json").read_text())
    cert, key = materials[identity]
    config["tls"].update(cafile=ca, certfile=cert, keyfile=key)
    (run_dir / f"remote_{identity}.json").write_text(json.dumps(config), encoding="utf-8")
print(run_dir)
PY
```

Templates use 127.0.0.1:8441–8443; ports must be free. Address changes must update party listeners, all peers, all client endpoints and certificate SANs. Keep certificate verification and `require_client_cert=true`. Relative certificate paths resolve from the JSON directory; this script writes absolute paths.

## Start and check parties

Continue in the same Bash session:

```bash
MPC_PARTY_PIDS=()
for MPC_PARTY_ID in 1 2 3; do
  .venv/bin/python -m mini_mpc.network.party_service \
    --config "$MPC_RUN_DIR/party$MPC_PARTY_ID.json" \
    > "$MPC_RUN_DIR/party$MPC_PARTY_ID.out" \
    2> "$MPC_RUN_DIR/party$MPC_PARTY_ID.events.jsonl" &
  MPC_PARTY_PIDS+=("$!")
done
```

Startup includes public-model interpolation. Check all three services with the insurer certificate. Connection errors wait up to 60 seconds per node; authorization and configuration errors fail directly.

```bash
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from time import monotonic, sleep
from mini_mpc.runtime.remote_backend import RemoteShamirBackend

config = Path(os.environ["MPC_RUN_DIR"]) / "remote_insurer.json"
backend = RemoteShamirBackend.from_config(config)
for client in backend.party_clients:
    deadline = monotonic() + 60
    while True:
        try:
            print(client.health())
            break
        except OSError:
            if monotonic() >= deadline:
                raise
            sleep(0.2)
PY
```

`/health` checks service liveness, not task readiness. Startup output is in `.out`; security events are in `.events.jsonl`. Check these files when a command fails.

## Run one audit

Shell arrays reuse local test parameters. Each command process receives only its own credentials. CLI role names cannot override certificate identity. MySQL defaults to 127.0.0.1:3306; override with role-specific `MINI_MPC_<ROLE>_MYSQL_HOST` and `PORT`.

```bash
MPC_AUDIT_ARGS=(--session-id audit-local-001 --patient-link-key patient-link-001 \
  --hospital-id hospital-001 --audit-date 2026-08-28)
MPC_INSURER_ARGS=(--inpatient-record-id insurer-inpatient-001 --audit-case-id audit-case-local-001)
MPC_EXPIRES_AT="$(.venv/bin/python -c 'import time; print(int(time.time()) + 3600)')"

MINI_MPC_INSURER_MYSQL_USER=mini_mpc_insurer_reader \
MINI_MPC_INSURER_MYSQL_PASSWORD=mini_mpc_insurer_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli insurer-register \
  --client-config "$MPC_RUN_DIR/remote_insurer.json" \
  "${MPC_AUDIT_ARGS[@]}" "${MPC_INSURER_ARGS[@]}" --expires-at "$MPC_EXPIRES_AT"

MINI_MPC_TELCO_MYSQL_USER=mini_mpc_telco_reader \
MINI_MPC_TELCO_MYSQL_PASSWORD=mini_mpc_telco_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli telco \
  --client-config "$MPC_RUN_DIR/remote_telco.json" "${MPC_AUDIT_ARGS[@]}"

MINI_MPC_HOSPITAL_MYSQL_USER=mini_mpc_hospital_reader \
MINI_MPC_HOSPITAL_MYSQL_PASSWORD=mini_mpc_hospital_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli hospital \
  --client-config "$MPC_RUN_DIR/remote_hospital.json" "${MPC_AUDIT_ARGS[@]}"

MINI_MPC_INSURER_MYSQL_USER=mini_mpc_insurer_reader \
MINI_MPC_INSURER_MYSQL_PASSWORD=mini_mpc_insurer_reader \
.venv/bin/python -m mini_mpc.applications.audit_cli insurer \
  --client-config "$MPC_RUN_DIR/remote_insurer.json" \
  "${MPC_AUDIT_ARGS[@]}" "${MPC_INSURER_ARGS[@]}"
```

Expected outputs are `status=registered`, two `status=submitted` responses, then `risk_level=medium`. Business output excludes scores and features. Telecom query authorization uses pre-seeded requests; dynamic request intake is absent.

## Retry and stop

Registration retries must keep the same manifest and expiry. While parties run and the task remains valid, rerun insurer computation: completed steps only acknowledge; failed BGW delivery resumes with original shares. Rerunning telecom or hospital submission generates new shares, not identical retries. Use a new session for another full run.

Concurrent distribution of one multiplication returns `in_progress`. Expired tasks cannot execute, return results or extend expiry. Party restarts lose session state. See the [threat model](threat_model.md#english).

```bash
kill "${MPC_PARTY_PIDS[@]}"
wait "${MPC_PARTY_PIDS[@]}" || true
docker compose stop mysql
```

The temporary directory holds test certificates and logs. It can be removed after shutdown. See [testing](testing.md#english) for commands and coverage.
