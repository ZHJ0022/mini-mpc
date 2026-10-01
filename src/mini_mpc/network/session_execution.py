"""Party-local execution progress for the deployed audit plan.

复用任务清单、公开指令和 share store，只补充执行进度与分发状态。
Reuse manifests, public instructions and share storage; track execution only.
"""

from collections.abc import Callable
from _thread import LockType
from dataclasses import dataclass, field
from threading import Lock

from mini_mpc.math.field import FieldElement
from mini_mpc.network.task_registry import TaskRegistry
from mini_mpc.runtime.computation_plan import AuditComputationPlan, ComputationStep
from mini_mpc.runtime.party import MPCParty
from mini_mpc.sharing.share import Share


class ExecutionError(RuntimeError):
    """Stable, secret-free execution failure for HTTP and event records.

    为 HTTP 与事件提供稳定且不含秘密的执行错误类别。
    Provide a stable execution error category without secret values.
    """

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class SessionProgress:
    """Mutable progress protected by one session lock.

    会话锁保护进度；秘密缓存禁止出现在对象 repr 中。
    A session lock protects progress; secret caches are excluded from repr.
    """

    # next_step_index 是下一条尚未完成的指令；此前步骤的重试直接回执。
    # next_step_index names the next incomplete instruction; earlier steps acknowledge retries.
    next_step_index: int = 0
    # sending 阻止同一乘法并发分发；锁外网络调用期间仍允许接收 reshares。
    # sending prevents concurrent distribution while permitting incoming peer reshares.
    sending: bool = False
    # reshares 缓存首次随机生成的发送份额；acknowledged_peers 仅记录成功回执。
    # reshares caches the original random shares; acknowledged_peers tracks successful replies.
    reshares: tuple[Share, ...] | None = field(default=None, repr=False)
    acknowledged_peers: set[int] = field(default_factory=set)
    lock: LockType = field(default_factory=Lock, repr=False)


class SessionExecutor:
    """Execute the existing plan with ordered, idempotent service operations.

    按现有计划执行有序、幂等操作；不复制任务清单或秘密份额存储。
    Execute ordered, idempotent operations without duplicating manifests or stores.
    """

    def __init__(
        self, party: MPCParty, tasks: TaskRegistry, plan: AuditComputationPlan
    ) -> None:
        self.party = party
        self.tasks = tasks
        self.plan = plan
        # 全局锁只保护状态索引；各会话用自己的锁，互不阻塞网络发送。
        # The global lock protects only the index; session locks keep unrelated sends independent.
        self._sessions: dict[str, SessionProgress] = {}
        self._lock = Lock()

    def _progress(self, session_id: str) -> SessionProgress:
        # 先验证任务，再分配状态，未登记请求不能制造会话。
        # Validate the task before allocating progress for a session.
        self.tasks.require(session_id)
        with self._lock:
            return self._sessions.setdefault(session_id, SessionProgress())

    def receive(
        self, session_id: str, secret_id: str, share: Share,
        *, multiplication: ComputationStep | None = None,
    ) -> bool:
        """Store an input or current BGW reshare; return whether it is a retry.

        保存机构输入或当前 BGW 重分享；返回是否为相同份额重试。
        Store an input or current reshare and identify identical retries.
        """

        progress = self._progress(session_id)
        with progress.lock:
            self.tasks.require(session_id)
            # 已存对象的重复与冲突由 ShareStore 判断，不维护另一份内容摘要。
            # ShareStore checks duplicates and conflicts; no second content digest is kept.
            # 按 key 查询，避免每次接收都复制所有会话的份额索引。
            # Look up one key instead of copying the share index across all sessions.
            try:
                self.party.get_share(session_id, secret_id)
            except KeyError:
                pass
            else:
                self.party.receive_share(session_id, secret_id, share)
                return True
            if multiplication is None:
                if progress.next_step_index != 0 or progress.sending:
                    raise ExecutionError("wrong_order", "input phase has ended")
            else:
                index = self.plan.require_step(multiplication)
                # 先分发的 peer 可先于本方分发到达；合并前的两个阶段均可接收。
                # Peers may send before our distribution; accept through the reduction stage.
                if progress.next_step_index not in (index, index + 1):
                    raise ExecutionError("wrong_order", "reshare is outside the current multiplication")
            self.party.receive_share(session_id, secret_id, share)
            return False

    def _ready(self, session_id: str, progress: SessionProgress, step: ComputationStep) -> bool:
        """Check order and prerequisites; True means a completed retry.

        校验顺序与输入；True 表示已经完成的步骤重试。
        Check order and inputs; True identifies a completed-step retry.
        """

        index = self.plan.require_step(step)
        if index < progress.next_step_index:
            return True
        if index != progress.next_step_index:
            raise ExecutionError("wrong_order", "computation step is out of order")
        if progress.sending:
            raise ExecutionError("in_progress", "computation step is already running")
        for secret_id in step.input_ids:
            try:
                self.party.get_share(session_id, secret_id)
            except KeyError as exc:
                raise ExecutionError("missing_input", "required input shares are missing") from exc
        return False

    def execute(self, session_id: str, step: ComputationStep) -> bool:
        """Commit a local constant or sum atomically, returning retry status.

        原子执行公开常量或本地求和，并返回重试状态。
        Atomically execute a constant or local sum and return retry status.
        """

        progress = self._progress(session_id)
        with progress.lock:
            self.tasks.require(session_id)
            if self._ready(session_id, progress, step):
                return True
            if step.kind == "constant":
                # 公开常量是零次份额，参数沿用已部署计划。
                # Public constants use degree-zero shares with deployed plan parameters.
                share = Share(
                    self.party.party_id, FieldElement(step.value, self.plan.modulus),
                    self.plan.threshold, self.plan.num_parties,
                )
                self.party.receive_share(session_id, step.output_id, share)
            elif step.kind == "sum":
                # 线性运算继续使用 party 的原实现，不另建份额计算路径。
                # Reuse the party's existing linear computation instead of a second share path.
                self.party.compute_weighted_sum_share(
                    session_id, step.input_ids, step.weights, step.output_id
                )
            else:
                raise ValueError("multiplication requires peer distribution")
            # 保存成功后在同一锁内推进；并发重试只能看到完整提交。
            # Advance after storage under the same lock, so retries see a complete commit.
            progress.next_step_index += 1
            return False

    def distribute(
        self, session_id: str, step: ComputationStep,
        prepare: Callable[[], tuple[Share, ...]], send: Callable[[Share], None],
    ) -> bool:
        """Resume BGW delivery using the original random shares.

        使用首次生成的随机份额续传；网络失败保留秘密缓存与确认进度。
        Resume original random shares, retaining cached shares and acknowledgements on failure.
        """

        progress = self._progress(session_id)
        with progress.lock:
            self.tasks.require(session_id)
            if self._ready(session_id, progress, step):
                return True
            if progress.reshares is None:
                progress.reshares = prepare()
            pending = tuple(
                share for share in progress.reshares
                if share.party_id not in progress.acknowledged_peers
            )
            progress.sending = True
        try:
            # 不持会话锁进行网络调用，避免自身 reshare 接收请求死锁。
            # Never hold the session lock across network calls, including self-delivery.
            for share in pending:
                self.tasks.require(session_id)
                try:
                    send(share)
                except Exception as exc:
                    # peer 异常可能含请求或证书路径；只向业务和事件提供固定错误。
                    # Peer exceptions may contain request or certificate paths; expose a fixed error.
                    raise ExecutionError("peer_unavailable", "peer delivery failed", 503) from exc
                with progress.lock:
                    self.tasks.require(session_id)
                    progress.acknowledged_peers.add(share.party_id)
            with progress.lock:
                self.tasks.require(session_id)
                progress.next_step_index += 1
                # 完成后释放发送缓存；接收方 ShareStore 仍保存本方协议份额。
                # Release the outbound cache; each recipient retains its local protocol shares.
                progress.reshares = None
                progress.acknowledged_peers.clear()
            return False
        finally:
            with progress.lock:
                progress.sending = False

    def result(self, session_id: str) -> Share:
        """Return a stable final share only after the complete plan finishes.

        仅在完整计划完成后返回固定最终份额；有效期内允许重复领取。
        Return the stable final share only after completion; live tasks allow repeated reads.
        """

        progress = self._progress(session_id)
        with progress.lock:
            self.tasks.require(session_id)
            if progress.next_step_index != len(self.plan.steps):
                raise ExecutionError("result_not_ready", "risk level is not ready")
            return self.party.get_share(session_id, "risk_level")
