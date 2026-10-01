"""Execution ordering, retry and concurrency tests without network dependencies.

不依赖网络的顺序、重试及并发测试，复用真实 party 与 BGW 份额生成。
Test ordering, retries and concurrency using actual parties and BGW share generation.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from mini_mpc.network.http_server import _weighted_product_reshares, reshare_secret_id
from mini_mpc.network.session_execution import ExecutionError, SessionExecutor
from mini_mpc.network.task_registry import TaskExpiredError, TaskManifest, TaskRegistry
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.runtime.computation_plan import AuditComputationPlan, ComputationStep, polynomial_steps
from mini_mpc.runtime.party import MPCParty, ShareConflictError
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from mini_mpc.sharing.share import Share


@pytest.fixture
def cluster(monkeypatch):
    # clock 可精确推进到截止点；短多项式验证执行逻辑，不复制业务查表算法。
    # clock controls exact expiry; a short polynomial tests execution without duplicating lookup logic.
    clock = [1000]
    monkeypatch.setattr("mini_mpc.network.task_registry.time", lambda: clock[0])
    features = ("telco_absence_hours", "telco_max_absence_streak", "clinical_need_score")
    plan = AuditComputationPlan(
        "insurance-audit-v1", features, 2, 3, 251,
        (ComputationStep("sum", "weighted_sum", features, (6, 4, 3)),
         *polynomial_steps("weighted_sum", (1, 2), "risk_level", 3)),
    )
    executors = []
    for party_id in (1, 2, 3):
        tasks = TaskRegistry(party_id)
        tasks.register(TaskManifest("audit", 1100, 2, 3, 251, plan.model_version, features))
        executors.append(SessionExecutor(MPCParty(party_id), tasks, plan))
    for secret_id, value in zip(features, (5, 3, 6), strict=True):
        for executor, share in zip(executors, share_secret(value, 2, 3, 251), strict=True):
            executor.receive("audit", secret_id, share)
    return executors, plan, clock


def _start_multiplication(executors, plan):
    for executor in executors:
        for step in plan.steps[:2]:
            executor.execute("audit", step)
    return plan.steps[2]


def _delivery(executor, executors, step):
    endpoints = tuple(MPCPartyEndpoint(index, "unused", index) for index in (1, 2, 3))

    def prepare():
        return _weighted_product_reshares(executor.party, "audit", *step.input_ids, endpoints)

    def send(share):
        executors[share.party_id - 1].receive(
            "audit", reshare_secret_id(step.output_id, executor.party.party_id),
            share, multiplication=step,
        )

    return prepare, send


def test_plan_order_inputs_conflicts_and_result_readiness(cluster):
    executors, plan, _ = cluster
    executor = executors[0]
    original_keys = executor.party.store.keys()
    with pytest.raises(ExecutionError, match="out of order"):
        executor.execute("audit", plan.steps[1])
    assert executor.party.store.keys() == original_keys
    # 即使内部代码放入输出，也不能绕过完整计划的完成状态。
    # Even an internally injected output cannot bypass plan completion.
    executor.party.receive_share("audit", "risk_level", share_secret(2, 2, 3, 251)[0])
    with pytest.raises(ExecutionError, match="not ready"):
        executor.result("audit")
    original = executor.party.get_share("audit", plan.feature_ids[0])
    assert executor.receive("audit", plan.feature_ids[0], original)
    with pytest.raises(ShareConflictError):
        executor.receive("audit", plan.feature_ids[0], Share(1, original.value + 1, 2, 3))
    executor.execute("audit", plan.steps[0])
    assert executor.execute("audit", plan.steps[0])
    with pytest.raises(PermissionError, match="configured audit plan"):
        executor.execute("audit", ComputationStep("sum", "weighted_sum", plan.feature_ids, (1, 0, 0)))


def test_missing_inputs_and_future_reshares_do_not_advance(cluster):
    executors, plan, _ = cluster
    executor = executors[0]
    executor.tasks.register(TaskManifest("empty", 1100, 2, 3, 251, plan.model_version, plan.feature_ids))
    with pytest.raises(ExecutionError, match="missing"):
        executor.execute("empty", plan.steps[0])
    with pytest.raises(ExecutionError, match="outside"):
        executor.receive("audit", reshare_secret_id(plan.steps[2].output_id, 2),
                         share_secret(4, 2, 3, 251)[0], multiplication=plan.steps[2])
    assert not any("__bgw_" in key[1] for key in executor.party.store.keys())


@pytest.mark.parametrize("lose_response", [False, True])
def test_partial_delivery_reuses_random_shares_and_acknowledgements(cluster, lose_response):
    executors, plan, _ = cluster
    step = _start_multiplication(executors, plan)
    executor = executors[0]
    prepare, deliver = _delivery(executor, executors, step)
    generated = []
    attempts = []

    def generate():
        shares = prepare()
        generated.append(shares)
        return shares

    def fail_once(share):
        attempts.append(share.party_id)
        if share.party_id == 2:
            if lose_response:
                deliver(share)
            raise OSError("secret-looking peer exception must not escape")
        deliver(share)

    with pytest.raises(ExecutionError, match="peer delivery failed"):
        executor.distribute("audit", step, generate, fail_once)
    assert attempts == [1, 2]
    with pytest.raises(ExecutionError, match="out of order"):
        executor.execute("audit", plan.steps[3])

    def retry(share):
        attempts.append(share.party_id)
        deliver(share)

    executor.distribute("audit", step, generate, retry)
    assert attempts == [1, 2, 2, 3]
    assert len(generated) == 1
    for target, share in zip(executors, generated[0], strict=True):
        assert target.party.get_share("audit", reshare_secret_id(step.output_id, 1)) == share
    # 已完成分发只回执，不能生成第二组随机份额或再次调用网络。
    # Completed distribution acknowledges only; it neither regenerates nor sends shares.
    assert executor.distribute("audit", step, generate, retry)
    assert len(generated) == 1
    assert attempts == [1, 2, 2, 3]


def test_complete_plan_and_replay_keep_final_shares_stable(cluster):
    executors, plan, _ = cluster
    step = _start_multiplication(executors, plan)
    for executor in executors:
        executor.distribute("audit", step, *_delivery(executor, executors, step))
    for executor in executors:
        for operation in plan.steps[3:]:
            executor.execute("audit", operation)
    outputs = [executor.result("audit") for executor in executors]
    assert reconstruct_secret(outputs).value == 121  # 1 + 2 * 60 mod 251
    for executor in executors:
        for operation in plan.steps:
            if operation.kind == "multiply":
                assert executor.distribute("audit", operation, *_delivery(executor, executors, operation))
            else:
                assert executor.execute("audit", operation)
        assert executor.result("audit") == outputs[executor.party.party_id - 1]
        # 完成后仍可确认原输入和旧重分享，冲突值不能覆盖任何已用份额。
        # Completion still permits identical input/reshare retries, never conflicting rewrites.
        for secret_id, multiplication in (
            (plan.feature_ids[0], None),
            (reshare_secret_id(step.output_id, 1), step),
        ):
            share = executor.party.get_share("audit", secret_id)
            assert executor.receive("audit", secret_id, share, multiplication=multiplication)
            with pytest.raises(ShareConflictError):
                executor.receive("audit", secret_id,
                                 Share(share.party_id, share.value + 1, 2, 3),
                                 multiplication=multiplication)


def test_concurrent_distribution_rejects_duplicate_without_blocking_receiver(cluster):
    executors, plan, _ = cluster
    step = _start_multiplication(executors, plan)
    executor = executors[0]
    prepare, deliver = _delivery(executor, executors, step)
    entered, release = Event(), Event()

    def send(share):
        # 自身接收必须成功，证明网络调用期间未持会话锁。
        # Self-delivery must succeed, proving network callbacks run outside the session lock.
        deliver(share)
        if share.party_id == 1:
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(executor.distribute, "audit", step, prepare, send)
        try:
            assert entered.wait(5)
            with pytest.raises(ExecutionError, match="already running"):
                executor.distribute("audit", step, prepare, deliver)
            # 同一会话不能提前降阶；另一会话可独立提交并计算。
            # This session cannot reduce early; a separate session can submit and compute.
            with pytest.raises(ExecutionError, match="out of order"):
                executor.execute("audit", plan.steps[3])
            executor.tasks.register(TaskManifest(
                "independent", 1100, 2, 3, 251, plan.model_version, plan.feature_ids
            ))
            for feature in plan.feature_ids:
                executor.receive("independent", feature, executor.party.get_share("audit", feature))
            assert executor.execute("independent", plan.steps[0]) is False
            assert executor.party.get_share("independent", "weighted_sum") == (
                executor.party.get_share("audit", "weighted_sum")
            )
        finally:
            release.set()
        assert running.result(timeout=5) is False


def test_expiry_blocks_retries_reads_and_partial_distribution(cluster):
    executors, plan, clock = cluster
    step = _start_multiplication(executors, plan)
    executor = executors[0]
    prepare, deliver = _delivery(executor, executors, step)
    sent = []

    def expire_after_delivery(share):
        deliver(share)
        sent.append(share.party_id)
        clock[0] = 1100

    with pytest.raises(TaskExpiredError):
        executor.distribute("audit", step, prepare, expire_after_delivery)
    assert sent == [1]
    assert not executor._sessions["audit"].sending
    for operation in (
        lambda: executor.execute("audit", plan.steps[0]),
        lambda: executor.distribute("audit", step, prepare, deliver),
        lambda: executor.receive("audit", plan.feature_ids[0], executor.party.get_share("audit", plan.feature_ids[0])),
        lambda: executor.result("audit"),
    ):
        with pytest.raises(TaskExpiredError):
            operation()


def test_concurrent_local_computation_commits_once(cluster, monkeypatch):
    executors, plan, _ = cluster
    executor = executors[0]
    entered, retry_started, release = Event(), Event(), Event()
    original = executor.party.compute_weighted_sum_share
    calls = []

    def compute(*args):
        # 暂停首次实际计算，让第二个相同请求在会话锁上等待。
        # Pause the actual computation while an identical request waits on the session lock.
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return original(*args)

    def retry():
        retry_started.set()
        return executor.execute("audit", plan.steps[0])

    monkeypatch.setattr(executor.party, "compute_weighted_sum_share", compute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(executor.execute, "audit", plan.steps[0])
        try:
            assert entered.wait(5)
            second = pool.submit(retry)
            assert retry_started.wait(5)
        finally:
            release.set()
        assert first.result(timeout=5) is False
        assert second.result(timeout=5) is True
    assert len(calls) == 1


def test_expiry_while_waiting_for_session_lock_rejects_computation(cluster, monkeypatch):
    executors, plan, clock = cluster
    executor = executors[0]
    progress = executor._progress("audit")
    checked = Event()
    original = executor.tasks.require
    initial_keys = executor.party.store.keys()

    def require(session_id):
        manifest = original(session_id)
        checked.set()
        return manifest

    monkeypatch.setattr(executor.tasks, "require", require)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with progress.lock:
            pending = pool.submit(executor.execute, "audit", plan.steps[0])
            assert checked.wait(5)
            # 请求入场时未过期，等待锁期间过期后仍必须拒绝。
            # Admission was live, but expiry while waiting must still reject the operation.
            clock[0] = 1100
        with pytest.raises(TaskExpiredError):
            pending.result(timeout=5)
    assert executor.party.store.keys() == initial_keys
    assert progress.next_step_index == 0
