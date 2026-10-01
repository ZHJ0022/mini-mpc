# 威胁模型与安全边界

[English](#english)

本项目验证多机构住院风险协同计算的协议与服务边界。数据为合成样本，权重和阈值未经过真实业务验证；风险等级只作为人工核查线索。

## 信任与隐私假设

计算参与方遵循半诚实（semi-honest）模型：按协议执行，但可以观察本方输入、随机性、份额和消息。默认三个计算节点、重构门限二；单个节点的份额不足以恢复秘密，两个节点合谋可以重构。

原始身份、位置和临床记录留在各机构本地。输入方本身知道自己的特征；每个 party 只持有本节点份额，包括协议中间份额。医保业务接口只交付最终等级，不能读取分数或任意中间 share。等级本身仍泄露区间信号。

节点独立进程不等于独立主机或组织。内存 backend 将所有 party 放在同一进程，仅用于对照和示例，不提供进程被攻破后的隔离保证。

## 身份与操作授权

正式服务要求 mTLS：验证客户端证书链，再按部署配置中的证书 SHA-256 指纹绑定已知身份。不接受请求体自报角色；受信 CA 签发但未登记的身份也被拒绝。

| 已认证身份 | 允许的业务操作 |
| --- | --- |
| insurer | 登记任务、调度固定计划、提交计划中的公开常量、领取最终等级 |
| telco | 提交两个运营商特征 |
| hospital | 提交临床风险特征 |
| party-1..3 | 提交本方来源标识匹配的 BGW 重分享 |
| 所有已配置身份 | 检查 `/health` |

未列出的操作默认拒绝。机构输入与重分享使用独立接口；份额必须属于当前接收 party，参数与任务一致。正式服务禁止任意 `GET /shares`，固定 `GET /results` 只接受会话标识并检查接收者。

任务登记绑定有效期、公开模型、参数和特征顺序。计算请求必须匹配固定权重、输入 / 输出标识与公开常量；peer 地址必须匹配可信配置，实际发送使用服务端地址。上述检查限制非预期请求，不能验证特征真实性或所有份额的代数一致性。

## 请求与启动边界

请求体上限为 64 KiB。接口要求单一合法 `Content-Length`，拒绝 chunked、重复长度和不完整请求体。超大请求返回 413，读取超时返回 408，其他帧错误返回 400；拒绝请求不写入份额。

接受连接后，在 TLS 握手前设置 5 秒 socket 超时；配置、公开计划及服务端证书加载成功后才绑定端口。份额入口先检查任务参数与权限，再构造有限域对象。这些是基本资源限制，没有连接总量、速率或完整任务预算控制。

## 执行与重试

- 输入与重分享内容不可冲突覆盖；相同内容可重发。
- 各会话独立维护下一步骤；越序、缺输入及未计划计算失败，完成步骤的精确重试只回执。
- BGW 缓存首次随机份额和已确认接收方，失败后续传；同一乘法并发分发拒绝，接收重分享仍可进行。
- 完整计划完成前不交付结果。执行、重试和领取均检查有效期，过期会话不能重新登记延长寿命。

这些是进程内保证，不是持久化全局 exactly-once。重新随机分享与重发原份额不同；运营商 / 医院 CLI 重跑会产生新份额，已有输入可能拒绝冲突。

## 日志与凭据

安全事件只允许 `timestamp`、`party_id`、`session_id`、`caller_identity`、`operation`、`decision`、`error_code`，不记录请求体、业务字段、明文特征、份额值或私钥。独立服务将 JSON 行写到 stderr，未知内部异常与 peer 失败使用固定错误说明。

TLS 握手失败发生在 HTTP handler 前，不属于应用事件流。任务元数据仍可能透露参与和时序信息；当前没有持久化日志审计或保留策略。

机构进程只使用本方 view-only 数据库账号；数据库 GRANT 约束实际读取。运行配置注入证书、私钥和 CA，公开模板只含路径及占位指纹。测试凭据不用于真实部署。

## 未提供的保证

- 恶意参与方安全、虚假本地特征检测或份额一致性证明。
- 达到重构门限的合谋、操作系统 / 运行时被攻破、侧信道或拒绝服务防护。
- 跨重启恢复与重放防护、持久化审计及完整的节点故障容错。
- PSI、真实身份匹配、完整任务选择隐藏、多租户审批或跨任务查询预算。
- 生产证书 / 密钥治理、跨机构部署隔离、业务有效性或合规认证。

清单、份额与发送缓存只存于内存。请求鉴权没有将半诚实协议升级为 malicious security；测试通过也不是形式化证明或安全审计。验证范围见 [testing.md](testing.md)。

---

# English

This project validates protocol and service boundaries for joint inpatient risk assessment. Data is synthetic. Weights and thresholds have no real-world validation. Levels guide manual review.

## Trust and privacy assumptions

Compute parties are semi-honest: they follow the protocol but inspect their inputs, randomness, shares and messages. Defaults are three parties and threshold two. One party cannot reconstruct a secret; two colluding parties can.

Raw identity, location and clinical records stay local. Input owners know their own features. Each party holds only its local input and intermediate shares. The insurer API receives only the final level, with no score or arbitrary-share reads. The level still reveals interval information.

Separate processes do not imply separate hosts or organizations. The memory backend holds all parties in one process for examples and reference tests. It offers no isolation after that process is compromised.

## Identity and operation authorization

Services require mTLS. They verify the client certificate chain, then map its SHA-256 fingerprint to a configured identity. Request bodies cannot declare identity. Certificates signed by a trusted CA but absent from the identity map are rejected.

| Authenticated identity | Allowed business operations |
| --- | --- |
| insurer | Register tasks, schedule the fixed plan, submit planned public constants, fetch the final level |
| telco | Submit its two telecom features |
| hospital | Submit its clinical risk feature |
| party-1..3 | Submit BGW reshares with a source matching its own identity |
| All configured identities | Check `/health` |

Other operations are denied. Organization inputs and reshares use separate endpoints. Shares must match the receiving party and task parameters. Services reject arbitrary `GET /shares`. Fixed `GET /results` accepts only a session ID and checks the receiver.

Registration binds expiry, model, parameters and feature order. Computation must match planned weights, input/output IDs and public constants. Peer addresses must match trusted configuration; sends use server-side addresses. These checks constrain requests but cannot verify feature truth or full algebraic consistency.

## Request and startup boundaries

Request bodies are limited to 64 KiB. The API requires one valid `Content-Length` and rejects chunked encoding, duplicate lengths and incomplete bodies. Oversized requests return 413, body-read timeouts return 408, and other framing errors return 400. Rejected requests do not store shares.

Accepted sockets receive a 5-second timeout before TLS handshakes. Configuration, the public plan and server certificates load before port binding. Share submission checks task parameters and permissions before field construction. These basic limits do not control connection totals, request rates or full task budgets.

## Execution and retries

- Conflicting inputs or reshares cannot overwrite stored values. Identical content can be resent.
- Each session tracks its next step. Wrong order, missing inputs and unplanned work fail. Exact retries of completed steps only acknowledge completion.
- BGW retains the original random shares and confirmed recipients. Failed delivery resumes. Concurrent distribution of one multiplication is rejected; receiving reshares remains possible.
- Results require the full plan. Execution, retries and result retrieval check expiry. Expired sessions cannot be re-registered to extend their lifetime.

These guarantees apply within a running process, not durable global exactly-once execution. Fresh sharing differs from resending original shares. Rerunning a telecom or hospital CLI produces new shares and may conflict with stored inputs.

## Logs and credentials

Security events allow only `timestamp`, `party_id`, `session_id`, `caller_identity`, `operation`, `decision` and `error_code`. They exclude request bodies, business fields, plaintext features, share values and private keys. Standalone services write JSON lines to stderr. Unexpected internal errors and peer failures use fixed error text.

TLS handshake failures occur before the HTTP handler, outside the application event stream. Task metadata can still reveal participation and timing. Durable log audit and retention policies are absent.

Organization processes use only their own view-only database credentials. MySQL grants enforce reads. Runtime configuration supplies certificates, private keys and CAs. Public templates contain paths and placeholder fingerprints. Test credentials are not deployment credentials.

## Guarantees not provided

- Malicious-party security, detection of false features or share-consistency proofs.
- Threshold collusion, compromised OS/runtime, side-channel or denial-of-service protection.
- Restart recovery, replay protection across restarts, durable audit or full node fault tolerance.
- PSI, real identity matching, full task-selection privacy, tenant approval or cross-task query budgets.
- Production key/certificate governance, deployment isolation, business validity or compliance certification.

Manifests, shares and send caches exist only in memory. Request authorization does not upgrade a semi-honest protocol to malicious security. Passing tests is not a formal proof or security audit. See [testing](testing.md#english).
