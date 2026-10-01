# 计算协议

[English](#english)

当前实现采用素数域上的 Shamir 分享与半诚实 BGW 算术协议。原始记录留在机构本地；MPC 处理派生整数特征，输出等级编码。运行安全约束见 [threat_model.md](threat_model.md)。

## 参数与分享

默认计算节点数 `n=3`，重构门限 `k=2`，多项式次数上界 `d=k−1=1`，素数模数 `p=251`。域必须容纳互异非零节点取值点、业务值域及查表点；当前最大分数为 130，不会在域内回绕。

对秘密 `s`，独立均匀选择随机系数：

```text
f(x) = s + a₁x + … + a_d x^d    （在 F_p 中）
share_i = f(i)
```

实现使用 `secrets.randbelow(p)` 生成随机系数。不同秘密不得复用同一组随机系数。`Share` 同时记录节点、域、门限及节点总数；不兼容份额不得混用。

至少 `k` 个有效份额可在零点重构：

```text
s = Σ_i λ_i · share_i
λ_i = ∏_{j≠i} (-x_j) / (x_i - x_j)
```

在标准 Shamir 模型下，少于 `k` 个份额不泄露秘密；达到门限的节点合谋可重构。模数 251 的选择是当前算术编码条件，不应按公钥密码密钥长度解释。

## 线性评分

相同节点的兼容份额可以本地相加或乘公开标量，无需交互。每个 party 计算：

```text
score_share_i = 6 × absence_share_i
              + 4 × streak_share_i
              + 3 × clinical_share_i
```

通用域运算支持负整数；当前业务查表从零开始，因此业务权重限为非负整数。

输出保持共享状态。业务调度接口只返回完成回执，不返回分数份额。

## BGW 乘法降阶

次数上界为 `d` 的输入多项式 `f`、`g` 本地相乘后，`h=f·g` 的次数最高为 `2d`。为保持后续运算的分享参数，协议要求 `n>2d` 并将乘积降为新的 `d` 次分享：

1. 节点 `i` 计算局部乘积 `h(i)=f(i)g(i)`。
2. 节点 `i` 用新的随机多项式 `r_i` 分享 `h(i)`，满足 `r_i(0)=h(i)`、次数不超过 `d`。
3. 发送前将各重分享乘以公开零点插值系数 `λ_i`，接收节点 `j` 得到 `q_i(j)=λ_i r_i(j)`。
4. 接收节点本地求和，得到 `u(j)=Σ_i q_i(j)`。

于是 `u(0)=Σ_i λ_i h(i)=h(0)`，且 `u` 的次数不超过 `d`。过程不重构乘积明文；每个接收方只得到自己的新份额。默认 `n=3`、`d=1` 满足条件，当前乘法仍需要全部三个节点的重分享。

## 共享分数上的等级转换

公开映射为：

| 分数区间 | 等级 | 编码 |
| --- | --- | --- |
| 0–19 | none | 0 |
| 20–44 | low | 1 |
| 45–79 | medium | 2 |
| 80–130 | high | 3 |

在 0–130 的 131 个点上插值得到公开多项式 `L`，使用 Horner 形式在共享分数上求值：

```text
acc = 最高次公开系数
逐项执行 acc = acc × shared_score + 公开系数
```

共享乘法使用 BGW；接收方先合并乘积重分享，再本地加上公开系数。最终各 party 保存 `risk_level` 份额，医保仅重构该编码。

多项式次数最高为 130，实际次数由系数决定。Horner 的共享乘法数量随次数增长，适合当前小值域，不是通用高性能比较方案。输入超出规定范围时，求值结果不保证有正确等级含义；当前依赖数据方本地范围检查和半诚实假设，没有恶意输入范围证明。

## 交付与限制

正式服务只接受部署模型对应的精确指令，按会话顺序执行；完整计划完成后，登记的医保接收者才能领取等级。少于门限的输出份额不能重构，业务接口不交付输入、分数或 Horner 中间份额。

等级会透露对应的分数区间，并可能与外部知识结合产生推断。选择等级是减少输出精度，不是零信息泄露。固定计算计划不等同于完整的跨任务查询控制。

当前不包含 Beaver triples、通用安全比较、浮点 / 固定点计算或 malicious security。Shamir/BGW 是既有协议；代码与测试验证实现流程，不构成新的安全证明。

协议来源：[Shamir 1979](https://doi.org/10.1145/359168.359176)、[Ben-Or、Goldwasser、Wigderson 1988](https://doi.org/10.1145/62212.62213)。

---

# English

The implementation uses Shamir sharing over a prime field and semi-honest BGW arithmetic. Raw records stay local. MPC processes derived integer features and outputs a level code. Service constraints are in the [threat model](threat_model.md#english).

## Parameters and sharing

Defaults are `n=3` parties, reconstruction threshold `k=2`, degree bound `d=k−1=1`, and prime modulus `p=251`. The field must fit distinct nonzero party points, business values and lookup points. The maximum score is 130, so it does not wrap.

For a secret `s`, sample independent uniform coefficients:

```text
f(x) = s + a₁x + … + a_d x^d    (in F_p)
share_i = f(i)
```

Coefficients use `secrets.randbelow(p)`. Never reuse one coefficient set for different secrets. `Share` records the party, field, threshold and party count. Incompatible shares cannot be mixed.

At least `k` valid shares reconstruct at zero:

```text
s = Σ_i λ_i · share_i
λ_i = ∏_{j≠i} (-x_j) / (x_i - x_j)
```

Standard Shamir sharing reveals no secret from fewer than `k` shares. A coalition reaching the threshold can reconstruct. Modulus 251 is an arithmetic encoding choice, not a public-key security parameter.

## Linear scoring

Compatible shares at one party support local addition and public scalar multiplication without interaction:

```text
score_share_i = 6 × absence_share_i
              + 4 × streak_share_i
              + 3 × clinical_share_i
```

Field arithmetic supports negative integers. The business lookup starts at zero, so business weights must be non-negative integers.

The score stays shared. Business scheduling returns completion status, not score shares.

## BGW multiplication and degree reduction

Multiplying degree-`d` polynomials `f` and `g` yields `h=f·g` of degree at most `2d`. The protocol requires `n>2d` and reduces the product to a fresh degree-`d` sharing:

1. Party `i` computes `h(i)=f(i)g(i)` locally.
2. It shares `h(i)` using a fresh polynomial `r_i`, with `r_i(0)=h(i)` and degree at most `d`.
3. Before sending, it scales each reshare by the public zero-point coefficient `λ_i`. Recipient `j` receives `q_i(j)=λ_i r_i(j)`.
4. Each recipient sums locally: `u(j)=Σ_i q_i(j)`.

Thus `u(0)=Σ_i λ_i h(i)=h(0)`, with degree at most `d`. The product is not revealed. Each recipient gets only its own new share. Defaults `n=3`, `d=1` satisfy the bound; current multiplication needs reshares from all three parties.

## Level conversion on a shared score

The public mapping is:

| Score | Level | Code |
| --- | --- | --- |
| 0–19 | none | 0 |
| 20–44 | low | 1 |
| 45–79 | medium | 2 |
| 80–130 | high | 3 |

Interpolate a public polynomial `L` over the 131 points in 0–130. Evaluate it on the shared score using Horner's method:

```text
acc = highest public coefficient
repeat: acc = acc × shared_score + next public coefficient
```

Shared multiplication uses BGW. Recipients combine product reshares before adding the public coefficient locally. Parties retain `risk_level` shares. The insurer reconstructs only the code.

The degree is at most 130; coefficients determine the actual degree. Shared multiplication count grows with degree. This suits a small range, not general fast comparison. Out-of-range inputs have no guaranteed level meaning. The current boundary relies on local range checks and semi-honest behavior, without malicious-input range proofs.

## Delivery and limits

Services accept only exact instructions for the deployed model, in session order. The registered insurer can fetch the level only after the full plan finishes. Fewer than the threshold output shares cannot reconstruct. Business endpoints deliver no input, score or Horner intermediate shares.

A level reveals a score interval and may support inference with outside knowledge. Lower output precision reduces leakage; it does not eliminate it. A fixed plan does not provide full query control across tasks.

Beaver triples, general secure comparison, floating-point or fixed-point arithmetic, and malicious security are absent. Shamir/BGW are established protocols. Code and tests validate the implementation flow, not a new security proof.

Sources: [Shamir 1979](https://doi.org/10.1145/359168.359176), [Ben-Or, Goldwasser and Wigderson 1988](https://doi.org/10.1145/62212.62213).
