from __future__ import annotations

import json
import socket
from hashlib import sha256
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from mini_mpc.math.field import FieldElement
from mini_mpc.network.authorization import authorize_operation, party_id_from_identity
from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.network.task_registry import TaskExpiredError, TaskManifest, TaskRegistry
from mini_mpc.network.security_events import record_security_event
from mini_mpc.network.session_execution import ExecutionError, SessionExecutor
from mini_mpc.network.tls import MPCPartyTLSConfig
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.runtime.party import MPCParty, ShareConflictError
from mini_mpc.runtime.computation_plan import AuditComputationPlan, ComputationStep
from mini_mpc.runtime.serde import share_from_dict, share_to_dict
from mini_mpc.sharing.shamir import lagrange_coefficients_at_zero, share_secret
from mini_mpc.sharing.share import Share


# 当前接口只发送小整数、份额及任务元数据；限制单个 JSON 请求的字节数。
# Requests contain small integers, shares and task metadata; cap each JSON body.
MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyTooLargeError(ValueError):
    """Request body exceeds the configured limit. / 请求体超过大小上限。"""


class MPCPartyHTTPServer(ThreadingHTTPServer):
    """HTTP/HTTPS service for one MPC party.

    每个服务只持有一个计算节点及其本方份额。
    """

    # 同时限制 TLS 握手与连接读写等待；不代表整个任务的执行截止时间。
    # Bound TLS handshake and socket I/O waits, not the whole MPC task duration.
    request_timeout: float = 5.0

    def __init__(
        self,
        server_address: tuple[str, int],
        party: MPCParty,
        *,
        tls_config: MPCPartyTLSConfig | None = None,
        peer_tls_config: MPCPartyTLSConfig | None = None,
        client_fingerprints: Mapping[str, str] | None = None,
        peer_endpoints: Sequence[MPCPartyEndpoint] = (),
        computation_plan: AuditComputationPlan | None = None,
        allow_unauthenticated_test_clients: bool = False,
    ) -> None:
        # 指纹来自部署配置；证书链先由 TLS 校验，再精确绑定节点身份。
        # TLS validates the chain; configured certificate pins then identify nodes.
        client_identities = {
            fingerprint.lower(): identity
            for identity, fingerprint in (client_fingerprints or {}).items()
        }
        if not allow_unauthenticated_test_clients and (
            tls_config is None
            or not tls_config.require_client_cert
            or not client_identities
        ):
            raise ValueError("party server requires mTLS and client identity mappings")
        self.party = party
        self.tasks = TaskRegistry(party.party_id)
        self.tls_config = tls_config
        self.peer_tls_config = peer_tls_config
        self.scheme = "https" if tls_config is not None else "http"
        self.client_identities = client_identities
        # peer_endpoints 只来自部署配置；computation_plan 由本地公开模型生成。
        # peer_endpoints come from deployment config; the plan comes from the local public model.
        self.peer_endpoints = tuple(peer_endpoints)
        self.computation_plan = computation_plan
        self.executor = (
            SessionExecutor(party, self.tasks, computation_plan)
            if computation_plan is not None else None
        )
        self.allow_unauthenticated_test_clients = allow_unauthenticated_test_clients
        if not allow_unauthenticated_test_clients and (
            computation_plan is None or not peer_endpoints
        ):
            raise ValueError("party server requires a computation plan and trusted peers")
        # 全部配置及证书加载成功后才监听；失败启动不占用端口。
        # Bind only after configuration and certificate loading succeed.
        self.tls_context = tls_config.server_context() if tls_config is not None else None
        super().__init__(server_address, MPCPartyRequestHandler)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        """Apply a timeout before wrapping an accepted socket in TLS.

        接收连接后先设置超时，再执行 TLS 握手；失败时关闭连接。
        """
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        try:
            if self.tls_context is not None:
                connection = self.tls_context.wrap_socket(connection, server_side=True)
            return connection, address
        except OSError:
            connection.close()
            raise


class MPCPartyRequestHandler(BaseHTTPRequestHandler):
    """HTTP/HTTPS API for party-local MPC operations.

    提供 party-local MPC 操作的 HTTP/HTTPS API。
    """

    server: MPCPartyHTTPServer

    def do_GET(self) -> None:
        """Handle health, result and reference-share requests.

        处理健康检查、结果领取及协议对照份额读取。
        """

        if not self._identify_client():
            return
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        # 拒绝读取时也保留单一会话标识；不记录完整 URL 或查询内容。
        # Retain one session id even for denied reads, never the full URL or query.
        session_values = query.get("session_id", [])
        if len(session_values) == 1:
            self.event_session_id = session_values[0]
        if parsed_url.path == "/health":
            self._send_json(
                200,
                {"status": "ok", "party_id": self.server.party.party_id},
            )
            return

        if parsed_url.path == "/shares":
            try:
                # 任意 secret 的网络读取只服务于显式的协议测试夹具。
                # Arbitrary network share reads exist only in explicit protocol test fixtures.
                if not self.server.allow_unauthenticated_test_clients:
                    raise PermissionError("arbitrary share reads are disabled")
                session_id = _single_query_value(query, "session_id")
                secret_id = _single_query_value(query, "secret_id")
                share = self.server.party.get_share(session_id, secret_id)
            except Exception as exc:
                self._send_error(exc)
                return
            self._send_json(200, {"share": share_to_dict(share)})
            return

        if parsed_url.path == "/results":
            try:
                if set(query) != {"session_id"}:
                    raise ValueError("result request requires only session_id")
                session_id = _single_query_value(query, "session_id")
                manifest = self._require_session(session_id)
                if manifest is not None:
                    # 接收者由已注册任务指定；客户端不能选择 secret_id。
                    # The registered task names the receiver; the client cannot choose a secret id.
                    authorize_operation(self.client_identity, "read_result")
                    if self.client_identity != manifest.receiver_id:
                        raise PermissionError("client is not the task result receiver")
                share = (
                    self.server.executor.result(session_id) if manifest is not None
                    else self.server.party.get_share(session_id, "risk_level")
                )
            except Exception as exc:
                self._send_error(exc)
                return
            self._send_json(200, {"share": share_to_dict(share)})
            return

        self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:
        """Handle share submission and party-local computation.

        处理 share 提交和 party 本地计算请求。
        Handle share submission and party-local computation requests.
        """

        if not self._identify_client():
            return
        if self.path == "/sessions":
            self._handle_register_task()
            return
        if self.path == "/shares":
            self._handle_submit_share(reshare=False)
            return
        if self.path == "/shares/reshares":
            self._handle_submit_share(reshare=True)
            return
        if self.path == "/shares/public":
            self._handle_submit_public_constant()
            return
        if self.path == "/compute/weighted-sum":
            self._handle_weighted_sum()
            return
        if self.path == "/compute/multiply/distribute-reshares":
            self._handle_distribute_reshares()
            return
        self._send_json(404, {"error": "unknown endpoint"})

    def _require_session(self, session_id: str) -> TaskManifest | None:
        """Check task existence before any business share operation.

        独立服务先验证会话；旧协议测试显式使用无鉴权 fixture。
        Standalone services check the session; explicit legacy fixtures may bypass it.
        """

        self.event_session_id = session_id
        if self.server.allow_unauthenticated_test_clients:
            return None
        return self.server.tasks.require(session_id)

    def _handle_register_task(self) -> None:
        try:
            if self.client_identity is None:
                raise PermissionError("registered client identity required")
            authorize_operation(self.client_identity, "register_task")
            manifest = TaskManifest.from_payload(self._read_json())
            self.event_session_id = manifest.session_id
            # 注册时先固定模型，后续计算不能借同一会话替换权重或参数。
            # Bind the model at registration before any session work can begin.
            self.server.computation_plan.validate_manifest(manifest)
            retry = self.server.tasks.register(manifest)
            self.event_decision = "retry" if retry else "allowed"
        except Exception as exc:
            self._send_error(exc)
            return
        self._send_json(200, {"status": "registered"})

    def _identify_client(self) -> bool:
        """Bind a TLS-verified certificate to one configured node identity.

        将 TLS 已验证的证书绑定到预配置身份，供后续权限检查使用。
        Bind the verified certificate to a configured identity for authorization.
        """

        # 每次请求重置事件上下文，避免 keep-alive 请求继承上次会话或身份。
        # Reset context per request so keep-alive requests cannot inherit prior identity or session.
        self.client_identity = None
        self.event_session_id = None
        self.event_decision = "allowed"
        self.event_error_code = None
        self.event_operation = {
            ("GET", "/health"): "health",
            ("GET", "/shares"): "read_share",
            ("GET", "/results"): "read_result",
            ("POST", "/sessions"): "register_task",
            ("POST", "/shares"): "submit_feature",
            ("POST", "/shares/reshares"): "submit_reshare",
            ("POST", "/shares/public"): "submit_public_constant",
            ("POST", "/compute/weighted-sum"): "compute_sum",
            ("POST", "/compute/multiply/distribute-reshares"): "distribute_reshares",
        }.get((self.command, urlparse(self.path).path), "unknown_operation")
        if not self.server.client_identities:
            # 仅显式的协议测试可绕过证书身份；独立服务必须启用 mTLS。
            # Only explicit protocol fixtures may bypass identity; standalone services require mTLS.
            self.client_identity = None
            return True
        certificate = self.connection.getpeercert(binary_form=True)
        if not certificate:
            self.event_error_code = "certificate_required"
            self._send_json(403, {"error": "client certificate required"})
            return False
        self.client_identity = self.server.client_identities.get(sha256(certificate).hexdigest())
        if self.client_identity is None:
            self.event_error_code = "unknown_identity"
            self._send_json(403, {"error": "unknown client identity"})
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        """Disable access logs that may contain URLs and task identifiers.

        关闭可能包含 URL 和任务标识的默认访问日志，使用白名单安全事件。
        """

        return

    def _handle_submit_share(self, *, reshare: bool) -> None:
        """Validate share ownership before storing either input or BGW data.

        机构输入与 party 重分享共用解析和存储，但使用不同权限规则。
        Parse and store both share kinds through one path with distinct permissions.
        """

        try:
            payload = self._read_json()
            session_id = _required_str(payload, "session_id")
            manifest = self._require_session(session_id)
            secret_id = _required_str(payload, "secret_id")
            share_payload = _required_mapping(payload, "share")
            multiplication = None
            # 先检查已登记参数；异常模数无需进入有限域的素数校验。
            # Check pinned parameters before field construction and primality checks.
            if manifest is not None and (
                _required_int(share_payload, "threshold") != manifest.threshold
                or _required_int(share_payload, "num_parties") != manifest.num_parties
                or _required_int(share_payload, "modulus") != manifest.modulus
            ):
                raise ValueError("share parameters differ from the registered task")
            if manifest is not None:
                if reshare:
                    sender_id = party_id_from_identity(self.client_identity)
                    if sender_id is None or sender_id > manifest.num_parties:
                        raise PermissionError("only a session party may submit reshares")
                    authorize_operation(
                        self.client_identity, "submit_reshare",
                        source_party_id=sender_id,
                    )
                    suffix = f"__bgw_reshare_from_{sender_id}"
                    if not secret_id.endswith(suffix) or len(secret_id) <= len(suffix):
                        raise PermissionError("reshare id does not match sender identity")
                    multiplication = self.server.computation_plan.require_reshare(secret_id, sender_id)
                else:
                    if secret_id not in manifest.feature_secret_ids:
                        raise PermissionError("feature is not registered for this task")
                    authorize_operation(
                        self.client_identity, "submit_feature", secret_id=secret_id
                    )
            # 参数与角色检查通过后，复用统一解析器构造并校验份额。
            # Reuse the share parser after parameter and role checks pass.
            share = share_from_dict(share_payload)
            if manifest is not None:
                retry = self.server.executor.receive(
                    session_id, secret_id, share, multiplication=multiplication
                )
                self.event_decision = "retry" if retry else "allowed"
            else:
                self.server.party.receive_share(session_id, secret_id, share)
        except Exception as exc:
            self._send_error(exc)
            return
        self._send_json(200, {"status": "stored"})

    def _handle_submit_public_constant(self) -> None:
        try:
            payload = self._read_json()
            session_id = _required_str(payload, "session_id")
            manifest = self._require_session(session_id)
            threshold = _required_int(payload, "threshold")
            num_parties = _required_int(payload, "num_parties")
            modulus = _required_int(payload, "modulus")
            if manifest is not None and (
                threshold != manifest.threshold
                or num_parties != manifest.num_parties
                or modulus != manifest.modulus
            ):
                raise ValueError("public constant parameters differ from the registered task")
            secret_id = _required_str(payload, "secret_id")
            value = _required_int(payload, "value")
            if manifest is not None:
                # 常量值和写入标识必须同时匹配公开计划。
                # Both the constant value and destination id must match the public plan.
                authorize_operation(self.client_identity, "submit_public_constant")
                # 执行器复用公开计划校验并构造常量份额，不重复实现。
                # The executor validates the public step and builds its share once.
                retry = self.server.executor.execute(
                    session_id, ComputationStep("constant", secret_id, value=value)
                )
                self.event_decision = "retry" if retry else "allowed"
            else:
                self.server.party.receive_share(
                    session_id, secret_id, Share(
                        self.server.party.party_id, FieldElement(value, modulus),
                        threshold, num_parties,
                    ),
                )
        except Exception as exc:
            self._send_error(exc)
            return
        self._send_json(200, {"status": "stored"})

    def _handle_weighted_sum(self) -> None:
        try:
            payload = self._read_json()
            session_id = _required_str(payload, "session_id")
            manifest = self._require_session(session_id)
            return_share = payload.get("return_share", True)
            if not isinstance(return_share, bool):
                raise TypeError("return_share must be a bool")
            input_ids = tuple(_required_str_sequence(payload, "input_secret_ids"))
            weights = tuple(_required_int_sequence(payload, "weights"))
            output_id = _required_str(payload, "output_secret_id")
            if manifest is not None:
                # 业务接口只回执状态；精确匹配输入、权重和输出后才计算。
                # Business calls return status only and compute after exact step matching.
                authorize_operation(self.client_identity, "compute")
                if return_share:
                    raise PermissionError("business computation cannot return a share")
                # 精确指令匹配和执行顺序都交给同一执行器检查。
                # One executor checks both the exact instruction and its execution order.
                retry = self.server.executor.execute(
                    session_id, ComputationStep("sum", output_id, input_ids, weights)
                )
                self.event_decision = "retry" if retry else "allowed"
            else:
                output_share = self.server.party.compute_weighted_sum_share(
                    session_id, input_ids, weights, output_id
                )
        except Exception as exc:
            self._send_error(exc)
            return
        if return_share:
            self._send_json(200, {"share": share_to_dict(output_share)})
        else:
            # 中间 share 保留在当前 party；调度方只收到完成状态。
            # Keep intermediate shares at the party; acknowledge only.
            self._send_json(200, {"status": "stored"})

    def _handle_distribute_reshares(self) -> None:
        try:
            payload = self._read_json()
            session_id = _required_str(payload, "session_id")
            manifest = self._require_session(session_id)
            left_id = _required_str(payload, "left_secret_id")
            right_id = _required_str(payload, "right_secret_id")
            output_id = _required_str(payload, "output_secret_id")
            requested_peers = tuple(_required_endpoint_sequence(payload, "peer_endpoints"))
            if manifest is not None:
                # 先核对完整目的地址，再使用服务端配置发送；拒绝重定向 BGW share。
                # Match every destination, then send using server config to prevent reshare redirection.
                authorize_operation(self.client_identity, "compute")
                self.server.computation_plan.require_step(
                    ComputationStep("multiply", output_id, (left_id, right_id))
                )
                if requested_peers != self.server.peer_endpoints:
                    raise PermissionError("peer endpoints differ from trusted configuration")
            peers = self.server.peer_endpoints if manifest is not None else requested_peers

            def prepare() -> tuple[Share, ...]:
                return _weighted_product_reshares(
                    self.server.party, session_id, left_id, right_id, peers
                )

            def send(share: Share) -> None:
                # 目标由已校验 peer 集合确定，缓存中只保存份额而非可变地址。
                # The validated peer set fixes destinations; caches contain shares, not mutable addresses.
                endpoint = next(peer for peer in peers if peer.party_id == share.party_id)
                MPCPartyHttpClient(endpoint, tls_config=self.server.peer_tls_config).submit_reshare(
                    session_id, reshare_secret_id(output_id, self.server.party.party_id), share
                )

            if manifest is not None:
                retry = self.server.executor.distribute(
                    session_id, ComputationStep("multiply", output_id, (left_id, right_id)),
                    prepare, send,
                )
                self.event_decision = "retry" if retry else "allowed"
            else:
                for share in prepare():
                    send(share)
        except Exception as exc:
            self._send_error(exc)
            return
        self._send_json(200, {"status": "sent"})

    def _read_json(self) -> dict[str, object]:
        # 固定长度 JSON 接口不接受分块编码或重复长度，避免请求边界歧义。
        # Reject chunking and duplicate lengths to keep JSON framing unambiguous.
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise ValueError("one non-negative Content-Length is required")
        length = int(lengths[0])
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLargeError("request body exceeds 64 KiB")
        raw_body = self.rfile.read(length)
        if len(raw_body) != length:
            raise ValueError("request body is incomplete")
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(payload, dict):
            raise TypeError("request payload must be a JSON object")
        # 只提取会话标识用于拒绝事件，绝不保留完整请求体。
        # Extract only the session id for rejection events; never retain the whole body.
        if isinstance(payload.get("session_id"), str):
            self.event_session_id = payload["session_id"]
        return payload

    def _send_error(self, exc: Exception) -> None:
        status = 400
        self.event_error_code = "invalid_request"
        if isinstance(exc, RequestBodyTooLargeError):
            status = 413
            self.event_error_code = "request_too_large"
        elif isinstance(exc, TimeoutError):
            status = 408
            self.event_error_code = "request_timeout"
        elif isinstance(exc, KeyError):
            status = 404
            self.event_error_code = "not_found"
        elif isinstance(exc, PermissionError):
            status = 403
            self.event_error_code = "task_expired" if isinstance(exc, TaskExpiredError) else "permission_denied"
        elif isinstance(exc, ShareConflictError):
            status = 409
            self.event_error_code = "share_conflict"
        elif isinstance(exc, ExecutionError):
            status = exc.status
            self.event_error_code = exc.code
        elif not isinstance(exc, (ValueError, TypeError)):
            status = 500
            self.event_error_code = "internal_error"
        # 未知异常可能包含路径或秘密；响应和日志都不暴露原始异常。
        # Unexpected exceptions may contain paths or secrets; do not expose their text.
        message = str(exc) if status != 500 else "internal request error"
        if status == 408:
            message = "request body read timed out"
        self._send_json(status, {"error": message, "error_code": self.event_error_code})

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        if not self.server.allow_unauthenticated_test_clients:
            record_security_event(
                self.server.party.party_id, self.client_identity, self.event_operation,
                "denied" if status >= 400 else self.event_decision,
                session_id=self.event_session_id,
                error_code=self.event_error_code or ("unknown_endpoint" if status == 404 else None),
            )
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_party_server(
    party_id: int,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    tls_config: MPCPartyTLSConfig | None = None,
    peer_tls_config: MPCPartyTLSConfig | None = None,
    client_fingerprints: Mapping[str, str] | None = None,
    peer_endpoints: Sequence[MPCPartyEndpoint] = (),
    computation_plan: AuditComputationPlan | None = None,
    allow_unauthenticated_test_clients: bool = False,
) -> MPCPartyHTTPServer:
    """Create an HTTP/HTTPS server for one MPC party.

    为单个 MPC party 创建 HTTP/HTTPS 服务；端口 0 表示由系统分配空闲端口。
    Create an HTTP/HTTPS server for one MPC party; port 0 asks the OS for a free
    port.
    """

    return MPCPartyHTTPServer(
        (host, port),
        MPCParty(party_id),
        tls_config=tls_config,
        peer_tls_config=peer_tls_config,
        client_fingerprints=client_fingerprints,
        peer_endpoints=peer_endpoints,
        computation_plan=computation_plan,
        allow_unauthenticated_test_clients=allow_unauthenticated_test_clients,
    )


def reshare_secret_id(output_secret_id: str, owner_party_id: int) -> str:
    """Return the temporary secret id for one BGW product reshare.

    返回某个 BGW 局部乘积 reshare 使用的临时 secret id。
    Return the temporary secret id used for one BGW local-product reshare.
    """

    return f"{output_secret_id}__bgw_reshare_from_{owner_party_id}"


def _weighted_product_reshares(
    party: MPCParty,
    session_id: str,
    left_secret_id: str,
    right_secret_id: str,
    peer_endpoints: Sequence[MPCPartyEndpoint],
) -> tuple[Share, ...]:
    """Generate weighted BGW shares separately from network delivery.

    复用现有局部乘积与降阶算法，将随机生成与网络发送分开以支持续传。
    Reuse the local product and reduction algorithm; separate generation from delivery for retries.
    """

    peer_ids = tuple(endpoint.party_id for endpoint in peer_endpoints)
    if party.party_id not in peer_ids:
        raise ValueError("peer_endpoints must include the local party")

    left_share = party.get_share(session_id, left_secret_id)
    right_share = party.get_share(session_id, right_secret_id)
    if left_share.threshold != right_share.threshold:
        raise ValueError("shares must use the same threshold")
    if left_share.num_parties != right_share.num_parties:
        raise ValueError("shares must use the same num_parties")
    if left_share.value.modulus != right_share.value.modulus:
        raise ValueError("shares must use the same modulus")

    threshold = left_share.threshold
    num_parties = left_share.num_parties
    modulus = left_share.value.modulus
    if 2 * (threshold - 1) >= num_parties:
        raise ValueError("BGW multiplication requires 2 * (threshold - 1) < num_parties")
    if set(peer_ids) != set(range(1, num_parties + 1)):
        raise ValueError("peer_endpoints must cover party ids 1..num_parties")

    product = left_share.value * right_share.value
    coefficients = lagrange_coefficients_at_zero(peer_ids, modulus)
    coefficient = coefficients[party.party_id].value
    reshares = share_secret(
        product.value,
        threshold=threshold,
        num_parties=num_parties,
        modulus=modulus,
    )
    # 每个发送方先乘自己的 Lagrange 系数；接收方后续只需把所有 reshares 相加。
    # Each sender applies its Lagrange coefficient first; receivers later only sum the reshares.
    return tuple(reshare * coefficient for reshare in reshares)


def _single_query_value(query: Mapping[str, list[str]], field_name: str) -> str:
    if field_name not in query or len(query[field_name]) != 1:
        raise ValueError(f"expected one query value for {field_name}")
    return query[field_name][0]


def _required_mapping(
    payload: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object]:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be an object")
    return value


def _required_str(payload: Mapping[str, object], field_name: str) -> str:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _required_int(payload: Mapping[str, object], field_name: str) -> int:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    # JSON 布尔值不是整数参数，尽管 bool 在 Python 中继承 int。
    # JSON booleans are not integer parameters, although Python bool subclasses int.
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _required_str_sequence(
    payload: Mapping[str, object],
    field_name: str,
) -> list[str]:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must be a list of strings")
    return value


def _required_int_sequence(
    payload: Mapping[str, object],
    field_name: str,
) -> list[int]:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if not isinstance(value, list) or not all(type(item) is int for item in value):
        raise TypeError(f"{field_name} must be a list of integers")
    return value


def _required_endpoint_sequence(
    payload: Mapping[str, object],
    field_name: str,
) -> list[MPCPartyEndpoint]:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")

    endpoints = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError(f"{field_name} entries must be objects")
        endpoints.append(
            MPCPartyEndpoint(
                party_id=_required_int(item, "party_id"),
                host=_required_str(item, "host"),
                port=_required_int(item, "port"),
                scheme=_optional_str(item, "scheme", "https"),
            )
        )
    return endpoints


def _optional_str(
    payload: Mapping[str, object],
    field_name: str,
    default: str,
) -> str:
    value = payload.get(field_name, default)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value
