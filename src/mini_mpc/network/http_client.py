from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlencode

from mini_mpc.network.tls import MPCPartyTLSConfig
from mini_mpc.network.task_registry import TaskManifest
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.runtime.serde import share_from_dict, share_to_dict
from mini_mpc.sharing.share import Share


class MPCPartyHttpClient:
    """Small HTTP/HTTPS client for one MPC party.

    面向单个 MPC party 的轻量 HTTP/HTTPS 客户端。
    """

    def __init__(
        self,
        endpoint: MPCPartyEndpoint,
        *,
        timeout: float = 5.0,
        tls_config: MPCPartyTLSConfig | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.tls_config = tls_config

    def register_task(self, manifest: TaskManifest) -> None:
        """Register one bounded audit before any feature submission.

        医保先向 party 登记有限期任务，再由数据方提交特征 shares。
        Register a bounded task before providers submit feature shares.
        """

        self._request("POST", "/sessions", manifest.to_payload())

    def submit_share(self, session_id: str, secret_id: str, share: Share) -> None:
        """Send one share to its remote party.

        把一份 share 发送给对应的远程 party。
        """

        self._request(
            "POST",
            "/shares",
            {
                "session_id": session_id,
                "secret_id": secret_id,
                "share": share_to_dict(share),
            },
        )

    def submit_reshare(self, session_id: str, secret_id: str, share: Share) -> None:
        """Send one BGW reshare from this party to a peer party.

        通过专用接口发送 BGW 重分享，避免和机构特征提交混用。
        Send a BGW reshare through its dedicated endpoint, separate from features.
        """

        self._request(
            "POST", "/shares/reshares",
            {
                "session_id": session_id,
                "secret_id": secret_id,
                "share": share_to_dict(share),
            },
        )

    def submit_public_constant(
        self,
        session_id: str,
        secret_id: str,
        value: int,
        *,
        threshold: int,
        num_parties: int,
        modulus: int,
    ) -> None:
        """Store a public constant as this party's degree-zero share.

        将公开常量作为本 party 的零次 share 保存。
        """

        self._request(
            "POST",
            "/shares/public",
            {
                "session_id": session_id,
                "secret_id": secret_id,
                "value": value,
                "threshold": threshold,
                "num_parties": num_parties,
                "modulus": modulus,
            },
        )

    def compute_weighted_sum_share(
        self,
        session_id: str,
        input_secret_ids: Sequence[str],
        weights: Sequence[int],
        output_secret_id: str,
    ) -> Share:
        """Ask the remote party to compute its local weighted output share.

        请求远程 party 只用本地 shares 计算加权 output share。
        Ask the remote party to compute the weighted output share using only
        its local shares.
        """

        payload = self._request(
            "POST",
            "/compute/weighted-sum",
            {
                "session_id": session_id,
                "input_secret_ids": list(input_secret_ids),
                "weights": list(weights),
                "output_secret_id": output_secret_id,
            },
        )
        return share_from_dict(_required_mapping(payload, "share"))

    def compute_weighted_sum(
        self,
        session_id: str,
        input_secret_ids: Sequence[str],
        weights: Sequence[int],
        output_secret_id: str,
    ) -> None:
        """Trigger local computation without receiving an intermediate share.

        业务路径只触发 party 本地计算；响应仅确认已存储，避免泄露分数 share。
        The business path receives an acknowledgement instead of a score share.
        """

        self._request(
            "POST",
            "/compute/weighted-sum",
            {
                "session_id": session_id,
                "input_secret_ids": list(input_secret_ids),
                "weights": list(weights),
                "output_secret_id": output_secret_id,
                "return_share": False,
            },
        )

    def distribute_multiplication_reshares(
        self,
        session_id: str,
        left_secret_id: str,
        right_secret_id: str,
        output_secret_id: str,
        peer_endpoints: Sequence[MPCPartyEndpoint],
    ) -> None:
        """Ask this party to send BGW product reshares to all peers.

        请求本 party 将 BGW 局部乘积 reshares 直接发送给所有 peers。
        Ask this party to send BGW local-product reshares directly to all
        peers.
        """

        self._request(
            "POST",
            "/compute/multiply/distribute-reshares",
            {
                "session_id": session_id,
                "left_secret_id": left_secret_id,
                "right_secret_id": right_secret_id,
                "output_secret_id": output_secret_id,
                "peer_endpoints": [
                    {
                        "party_id": endpoint.party_id,
                        "host": endpoint.host,
                        "port": endpoint.port,
                        "scheme": endpoint.scheme,
                    }
                    for endpoint in peer_endpoints
                ],
            },
        )

    def get_share(self, session_id: str, secret_id: str) -> Share:
        """Fetch one stored share from the remote party.

        从显式协议测试服务读取任意 share；正式服务禁止此接口。
        Fetch an arbitrary share from explicit protocol test servers only.
        """

        query = urlencode({"session_id": session_id, "secret_id": secret_id})
        payload = self._request("GET", f"/shares?{query}", None)
        return share_from_dict(_required_mapping(payload, "share"))

    def get_result_share(self, session_id: str) -> Share:
        """Fetch this party's final risk-level share for the registered receiver.

        只传任务标识；服务端校验接收者并固定读取最终 risk_level share。
        Send only the session id; the server checks the receiver and fixes risk_level.
        """

        query = urlencode({"session_id": session_id})
        payload = self._request("GET", f"/results?{query}", None)
        return share_from_dict(_required_mapping(payload, "share"))

    def health(self) -> dict[str, object]:
        """Check whether the remote party service is alive.

        检查远程 party 服务是否可用。
        Check whether the remote party service is reachable.
        """

        return self._request("GET", "/health", None)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None,
    ) -> dict[str, object]:
        """Send one JSON request with the standard library client.

        使用标准库 HTTP/HTTPS 客户端发送一次 JSON 请求，避免为原型引入 Web 框架依赖。
        Send one JSON request with the standard-library HTTP/HTTPS client to
        avoid a web-framework dependency for this prototype.
        """

        connection = self._connection()
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw_body = response.read()
        finally:
            connection.close()

        response_payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(response_payload, dict):
            raise TypeError("response payload must be a JSON object")
        if response.status >= 400:
            message = response_payload.get("error", response.reason)
            raise RuntimeError(f"{method} {path} failed: {message}")
        return response_payload

    def _connection(self) -> HTTPConnection:
        """Create a connection for the endpoint scheme.

        按 endpoint scheme 创建连接；HTTPS 使用本方证书及可信 CA。
        HTTPS connections use the caller's certificate and configured CA.
        """

        if self.endpoint.scheme == "http":
            return HTTPConnection(
                self.endpoint.host,
                self.endpoint.port,
                timeout=self.timeout,
            )
        if self.endpoint.scheme == "https":
            tls_config = self.tls_config or MPCPartyTLSConfig()
            return HTTPSConnection(
                self.endpoint.host,
                self.endpoint.port,
                timeout=self.timeout,
                context=tls_config.client_context(),
            )
        raise ValueError(f"unsupported party endpoint scheme: {self.endpoint.scheme}")


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
