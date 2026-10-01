"""HTTP 请求大小、连接超时与不完整输入。 / HTTP framing, size and timeout boundaries."""

import json
import socket
from threading import Thread

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.network.http_server import MAX_REQUEST_BODY_BYTES, make_party_server
from mini_mpc.runtime.serde import share_to_dict
from mini_mpc.sharing.shamir import share_secret
from network_test_support import RunningPartyClients


@pytest.fixture(scope="module")
def bounded_service():
    # 资源限制由同一服务实现；此夹具只关闭业务身份检查。
    # The same server enforces limits; this fixture disables business identity checks only.
    fixture = RunningPartyClients(1)
    with fixture as clients:
        yield fixture.servers[0], clients[0]


@pytest.mark.parametrize("headers,status", [
    ([], 400),
    ([("Content-Length", "-1")], 400),
    ([("Content-Length", "bad")], 400),
    ([("Content-Length", "2"), ("Content-Length", "2")], 400),
    ([("Content-Length", str(MAX_REQUEST_BODY_BYTES + 1))], 413),
    ([("Content-Length", "2"), ("Transfer-Encoding", "chunked")], 400),
])
def test_rejected_body_framing_does_not_store_shares(bounded_service, headers, status):
    server, client = bounded_service
    before = server.party.store.keys()
    connection = client._connection()
    try:
        connection.putrequest("POST", "/shares")
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(b"{}")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == status
        assert payload["error_code"] == ("request_too_large" if status == 413 else "invalid_request")
        assert server.party.store.keys() == before
    finally:
        connection.close()


@pytest.mark.parametrize("at_limit", [False, True])
def test_json_bodies_up_to_the_limit_are_accepted(bounded_service, at_limit):
    server, client = bounded_service
    session_id = f"body-limit-{at_limit}"
    payload = {
        "session_id": session_id,
        "secret_id": "input",
        "share": share_to_dict(share_secret(5, 1, 1, 251)[0]),
        "padding": "",
    }
    body = json.dumps(payload).encode()
    if at_limit:
        # padding 仅填满参考接口的请求体，验证上限按字节计且包含边界。
        # Fill the reference body to verify the inclusive byte limit.
        payload["padding"] = "x" * (MAX_REQUEST_BODY_BYTES - len(body))
        body = json.dumps(payload).encode()
        assert len(body) == MAX_REQUEST_BODY_BYTES
    connection = client._connection()
    try:
        connection.request("POST", "/shares", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "stored"}
        assert server.party.get_share(session_id, "input").value == FieldElement(5, 251)
    finally:
        connection.close()


def test_body_read_timeout_returns_408_without_mutation(bounded_service, monkeypatch):
    server, client = bounded_service
    monkeypatch.setattr(server, "request_timeout", 0.2)
    before = server.party.store.keys()
    connection = client._connection()
    try:
        connection.putrequest("POST", "/shares")
        connection.putheader("Content-Length", "20")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 408
        assert json.loads(response.read())["error_code"] == "request_timeout"
        assert server.party.store.keys() == before
    finally:
        connection.close()


def test_idle_tls_handshake_does_not_block_next_client(bounded_service, monkeypatch):
    server, client = bounded_service
    monkeypatch.setattr(server, "request_timeout", 0.2)
    # 首个连接不发送 TLS 数据；超时后正常客户端仍可完成健康检查。
    # Leave the first handshake idle; the following client must still complete.
    with socket.create_connection(server.server_address, timeout=2) as idle:
        assert client.health() == {"status": "ok", "party_id": 1}
        assert idle.recv(1) == b""


def test_short_body_is_rejected_after_peer_finishes_sending():
    # 半关闭写入端模拟提前 EOF；测试专用 HTTP 避免 TLS 关闭通知影响帧检查。
    # Half-close the writer to simulate EOF; test-only HTTP isolates body framing.
    with make_party_server(1, allow_unauthenticated_test_clients=True) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as connection:
                connection.sendall(
                    b"POST /shares HTTP/1.0\r\nHost: localhost\r\nContent-Length: 3\r\n\r\n{}"
                )
                connection.shutdown(socket.SHUT_WR)
                response = connection.recv(4096)
                assert b" 400 " in response
            assert server.party.store.keys() == ()
        finally:
            server.shutdown()
            thread.join(timeout=2)
