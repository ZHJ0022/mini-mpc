import json
import logging
from pathlib import Path
import ssl
from threading import Thread

import pytest

from mini_mpc.network.config import (
    load_remote_client_config,
    load_party_service_config,
    party_service_config_from_dict,
)
from mini_mpc.runtime.remote_backend import RemoteShamirBackend
from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.network.http_server import make_party_server
from mini_mpc.network.party_service import build_party_server
from mini_mpc.network.tls import MPCPartyTLSConfig
from mini_mpc.network.transport import MPCPartyEndpoint
from network_test_support import (
    write_test_pki,
)


TEST_CERT_FINGERPRINT = "1" * 64

# 独立服务从本地配置取得 BGW 目标；端口仅用于不执行乘法的单服务测试。
# Standalone parties take BGW destinations from config; these ports serve tests without multiplication.
TEST_PEER_ENDPOINTS = [
    {"party_id": party_id, "host": "127.0.0.1", "port": 8440 + party_id}
    for party_id in (1, 2, 3)
]


def test_party_server_requires_explicit_test_mode_without_mtls():
    # 普通服务构造默认拒绝无身份请求；协议 fixture 必须显式声明测试模式。
    # Normal construction denies anonymous clients; protocol fixtures opt in explicitly.
    with pytest.raises(ValueError, match="requires mTLS"):
        make_party_server(1)


def test_party_service_config_loads_tls_paths_relative_to_config_file(tmp_path):
    # 相对证书路径按配置文件目录解析，便于整体移动部署目录。
    # Resolve relative certificate paths from the config file directory.
    config_path = tmp_path / "party1.json"
    config_path.write_text(
        json.dumps(
            {
                "party_id": 1,
                "host": "127.0.0.1",
                "port": 0,
                "tls": {
                    "certfile": "certs/party.crt",
                    "keyfile": "certs/party.key",
                    "cafile": "certs/ca.crt",
                    "require_client_cert": True,
                },
                "client_fingerprints": {"insurer": TEST_CERT_FINGERPRINT},
                "peer_endpoints": TEST_PEER_ENDPOINTS,
            }
        ),
        encoding="utf-8",
    )

    config = load_party_service_config(config_path)

    assert config.party_id == 1
    assert config.port == 0
    assert config.tls.certfile == str(tmp_path / "certs" / "party.crt")
    assert config.tls.keyfile == str(tmp_path / "certs" / "party.key")
    assert config.peer_tls.cafile == str(tmp_path / "certs" / "ca.crt")


def test_party_service_config_rejects_invalid_required_fields():
    # 打开 socket 前拒绝非法 party_id、端口和缺失证书。
    # Reject invalid party_id, port, and missing certificates before binding.
    valid_payload = {
        "party_id": 1,
        "host": "127.0.0.1",
        "port": 0,
        "tls": {
            "certfile": "party.crt",
            "keyfile": "party.key",
            "cafile": "ca.crt",
            "require_client_cert": True,
        },
        "client_fingerprints": {"insurer": TEST_CERT_FINGERPRINT},
        "peer_endpoints": TEST_PEER_ENDPOINTS,
    }

    invalid_party = dict(valid_payload, party_id=0)
    with pytest.raises(ValueError):
        party_service_config_from_dict(invalid_party)

    invalid_port = dict(valid_payload, port=70000)
    with pytest.raises(ValueError):
        party_service_config_from_dict(invalid_port)

    missing_key = dict(valid_payload, tls={"certfile": "party.crt"})
    with pytest.raises(ValueError):
        party_service_config_from_dict(missing_key)

    no_client_auth = dict(valid_payload, tls={
        "certfile": "party.crt", "keyfile": "party.key", "cafile": "ca.crt"
    })
    with pytest.raises(ValueError, match="requires mTLS"):
        party_service_config_from_dict(no_client_auth)


def test_remote_client_config_builds_three_party_backend(tmp_path):
    # 客户端配置统一约束 party ids、协议参数和 CA 路径。
    # Client config validates party ids, protocol settings, and the CA path.
    config_path = tmp_path / "remote.json"
    config_path.write_text(json.dumps({
        "threshold": 2, "num_parties": 3, "modulus": 251,
        "endpoints": [
            {"party_id": party_id, "host": "localhost", "port": 8440 + party_id}
            for party_id in (1, 2, 3)
        ],
        "tls": {
            "cafile": "certs/ca.crt",
            "certfile": "certs/insurer.crt",
            "keyfile": "certs/insurer.key",
        },
    }), encoding="utf-8")

    config = load_remote_client_config(config_path)
    backend = RemoteShamirBackend.from_config(str(config_path))

    assert config.tls.cafile == str(tmp_path / "certs" / "ca.crt")
    assert config.tls.certfile == str(tmp_path / "certs" / "insurer.crt")
    assert tuple(client.endpoint.party_id for client in backend.party_clients) == (1, 2, 3)


def test_build_party_server_from_config_starts_https_health_endpoint(tmp_path):
    # 独立进程入口和测试复用同一 server 构造路径。
    # Reuse the same server builder for CLI startup and tests.
    ca, materials, fingerprints = write_test_pki(tmp_path, ("party-1", "insurer"))
    certfile, keyfile = materials["party-1"]
    config = party_service_config_from_dict(
        {
            "party_id": 1,
            "host": "127.0.0.1",
            "port": 0,
            "tls": {
                "certfile": str(certfile),
                "keyfile": str(keyfile),
                "cafile": ca,
                "require_client_cert": True,
            },
            "client_fingerprints": fingerprints,
            "peer_endpoints": TEST_PEER_ENDPOINTS,
        }
    )
    server = build_party_server(config)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        client = MPCPartyHttpClient(
            MPCPartyEndpoint(party_id=1, host=host, port=port, scheme="https"),
            tls_config=config.peer_tls,
        )

        assert client.health() == {"status": "ok", "party_id": 1}
        # 握手期间服务端拒绝未出示客户端证书的请求。
        # The server rejects missing client certificates during the TLS handshake.
        no_cert_client = MPCPartyHttpClient(
            MPCPartyEndpoint(party_id=1, host=host, port=port, scheme="https"),
            tls_config=type(config.peer_tls)(cafile=ca),
        )
        with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
            no_cert_client.health()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_trusted_but_unregistered_client_certificate_is_rejected(tmp_path, caplog):
    # CA 信任只验证证书链；节点身份还必须出现在部署配置中。
    # CA trust verifies the chain; deployment config must also name the node.
    ca, materials, fingerprints = write_test_pki(
        tmp_path, ("party-1", "insurer", "telco")
    )
    server_cert, server_key = materials["party-1"]
    config = party_service_config_from_dict({
        "party_id": 1,
        "tls": {
            "certfile": server_cert, "keyfile": server_key,
            "cafile": ca, "require_client_cert": True,
        },
        "client_fingerprints": {"insurer": fingerprints["insurer"]},
        "peer_endpoints": TEST_PEER_ENDPOINTS,
    })
    server = build_party_server(config)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        endpoint = MPCPartyEndpoint(1, host, port)
        insurer_cert, insurer_key = materials["insurer"]
        insurer = MPCPartyHttpClient(
            endpoint,
            tls_config=type(config.tls)(
                cafile=ca, certfile=insurer_cert, keyfile=insurer_key
            ),
        )
        assert insurer.health() == {"status": "ok", "party_id": 1}

        telco_cert, telco_key = materials["telco"]
        telco = MPCPartyHttpClient(
            endpoint,
            tls_config=type(config.tls)(
                cafile=ca, certfile=telco_cert, keyfile=telco_key
            ),
        )
        caplog.set_level(logging.INFO, logger="mini_mpc.security")
        with pytest.raises(RuntimeError, match="unknown client identity"):
            telco.health()
        assert server.party.store.keys() == ()
        assert server.tasks._tasks == {}
        assert server.executor._sessions == {}
        events = [json.loads(record.message) for record in caplog.records
                  if record.name == "mini_mpc.security"]
        assert len(events) == 1
        assert (events[0]["decision"], events[0]["error_code"]) == ("denied", "unknown_identity")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("field,invalid", [
    ("party_id", True), ("party_id", 1.0), ("port", True), ("port", 8441.0),
])
def test_party_config_rejects_boolean_and_float_integers(field, invalid):
    payload = json.loads((Path(__file__).resolve().parents[1] / "configs/examples/party1.json").read_text())
    payload[field] = invalid
    with pytest.raises(TypeError, match="integer"):
        party_service_config_from_dict(payload)


@pytest.mark.parametrize("party_id", [0, 4])
def test_party_config_requires_fixed_three_party_ids(party_id):
    payload = json.loads((Path(__file__).resolve().parents[1] / "configs/examples/party1.json").read_text())
    payload["party_id"] = party_id
    with pytest.raises(ValueError, match="between 1 and 3"):
        party_service_config_from_dict(payload)


@pytest.mark.parametrize("field", ["threshold", "num_parties", "modulus"])
def test_remote_config_rejects_boolean_protocol_parameters(tmp_path, field):
    payload = json.loads((Path(__file__).resolve().parents[1] / "configs/examples/remote_client.json").read_text())
    payload[field] = True
    config = tmp_path / "remote.json"
    config.write_text(json.dumps(payload))
    with pytest.raises(TypeError, match="integer"):
        load_remote_client_config(config)


def test_remote_config_rejects_composite_modulus(tmp_path):
    payload = json.loads((Path(__file__).resolve().parents[1] / "configs/examples/remote_client.json").read_text())
    payload["modulus"] = 253
    config = tmp_path / "remote.json"
    config.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="prime"):
        load_remote_client_config(config)


def test_server_loads_tls_before_binding_port(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer

    def unexpected_bind(server):
        pytest.fail("a failed TLS configuration must not bind a socket")

    monkeypatch.setattr(ThreadingHTTPServer, "server_bind", unexpected_bind)
    with pytest.raises(FileNotFoundError):
        make_party_server(
            1, allow_unauthenticated_test_clients=True,
            tls_config=MPCPartyTLSConfig(
                certfile=str(tmp_path / "missing.crt"), keyfile=str(tmp_path / "missing.key"),
            ),
        )


def test_server_requires_plan_before_binding_port(monkeypatch):
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(ThreadingHTTPServer, "server_bind", lambda server: pytest.fail("bound too early"))
    with pytest.raises(ValueError, match="computation plan"):
        make_party_server(
            1, tls_config=MPCPartyTLSConfig(require_client_cert=True),
            client_fingerprints={"insurer": TEST_CERT_FINGERPRINT},
        )
