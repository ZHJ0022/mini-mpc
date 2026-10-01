import json
from time import time
from pathlib import Path
import shutil
import ssl
import subprocess
from hashlib import sha256
from tempfile import TemporaryDirectory
from threading import Thread

from mini_mpc.network.http_client import MPCPartyHttpClient
from mini_mpc.network.http_server import make_party_server
from mini_mpc.network.tls import MPCPartyTLSConfig
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.network.config import party_service_config_from_dict
from mini_mpc.network.party_service import build_party_server
from mini_mpc.runtime.remote_protocol import SharingConfig, remote_register_task
from mini_mpc.runtime.computation_plan import AuditComputationPlan


def write_test_pki(
    directory: Path,
    identities: tuple[str, ...],
) -> tuple[str, dict[str, tuple[str, str]], dict[str, str]]:
    """Create short-lived node certificates under a temporary test directory.

    在测试临时目录签发独立节点证书；调用方负责清理目录。
    Issue distinct short-lived node certificates in a temporary test directory.
    """

    if shutil.which("openssl") is None:
        import pytest

        pytest.skip("OpenSSL command is required for mTLS integration tests")
    directory.mkdir(parents=True, exist_ok=True)
    ca_key = directory / "ca.key"
    ca_cert = directory / "ca.crt"

    def run(*args: str) -> None:
        subprocess.run(
            ["openssl", *args], check=True, capture_output=True, text=True
        )

    run(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
        "-days", "2", "-subj", "/CN=mini-mpc-test-ca",
        "-keyout", str(ca_key), "-out", str(ca_cert),
    )
    extensions = directory / "leaf.ext"
    extensions.write_text(
        "basicConstraints=CA:FALSE\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
        "subjectAltName=IP:127.0.0.1,DNS:localhost\n",
        encoding="ascii",
    )
    materials = {}
    fingerprints = {}
    for identity in identities:
        cert = directory / f"{identity}.crt"
        key = directory / f"{identity}.key"
        request = directory / f"{identity}.csr"
        run(
            "req", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-subj", f"/CN={identity}", "-keyout", str(key),
            "-out", str(request),
        )
        run(
            "x509", "-req", "-in", str(request), "-CA", str(ca_cert),
            "-CAkey", str(ca_key), "-CAcreateserial", "-days", "2",
            "-sha256", "-extfile", str(extensions), "-out", str(cert),
        )
        materials[identity] = (str(cert), str(key))
        fingerprints[identity] = sha256(
            ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
        ).hexdigest()
    return str(ca_cert), materials, fingerprints


class RunningPartyClients:
    """Explicit unauthenticated protocol reference fixture.

    仅供通用协议对照，显式关闭客户端身份检查；业务验收用 RunningAuditClients。
    Protocol references only; business acceptance uses RunningAuditClients.
    """

    def __init__(self, num_parties: int) -> None:
        self.num_parties = num_parties
        self.servers = []
        self.threads = []
        self.cert_dir = TemporaryDirectory()

    def __enter__(self) -> list[MPCPartyHttpClient]:
        ca, certfile, keyfile = self._write_test_tls_material()
        server_tls = MPCPartyTLSConfig(certfile=certfile, keyfile=keyfile)
        client_tls = MPCPartyTLSConfig(cafile=ca)
        clients = []
        for party_id in range(1, self.num_parties + 1):
            server = make_party_server(
                party_id,
                tls_config=server_tls,
                peer_tls_config=client_tls,
                allow_unauthenticated_test_clients=True,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append(server)
            self.threads.append(thread)
            host, port = server.server_address
            clients.append(
                MPCPartyHttpClient(
                    MPCPartyEndpoint(
                        party_id=party_id,
                        host=host,
                        port=port,
                        scheme="https",
                    ),
                    tls_config=client_tls,
                )
            )
        return clients

    def __exit__(self, exc_type, exc, traceback) -> None:
        # 显式关闭服务，避免测试结束后端口和线程残留。
        # Explicit shutdown prevents ports and threads from leaking after tests.
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.cert_dir.cleanup()

    def _write_test_tls_material(self) -> tuple[str, str, str]:
        # 协议对照仅校验服务端证书；业务夹具另用六个 mTLS 身份。
        # Reference tests verify the server; business tests use six mTLS identities.
        ca, materials, _ = write_test_pki(Path(self.cert_dir.name), ("party-1",))
        cert, key = materials["party-1"]
        return ca, cert, key


class RunningAuditClients(RunningPartyClients):
    """Actual deployed model with independent mTLS identities for every role.

    复用服务构造和清理逻辑，用独立证书运行正式模型及完整会话状态。
    Reuse service construction and cleanup with distinct certificates and full execution state.
    """

    def __init__(self) -> None:
        super().__init__(3)
        self.role_clients = {}

    def __enter__(self) -> "RunningAuditClients":
        identities = ("insurer", "telco", "hospital", "party-1", "party-2", "party-3")
        ca, materials, fingerprints = write_test_pki(Path(self.cert_dir.name), identities)
        try:
            for party_id in (1, 2, 3):
                cert, key = materials[f"party-{party_id}"]
                config = party_service_config_from_dict({
                    "party_id": party_id,
                    "tls": {"certfile": cert, "keyfile": key, "cafile": ca,
                            "require_client_cert": True},
                    "client_fingerprints": fingerprints,
                    "peer_endpoints": [
                        {"party_id": index, "host": "127.0.0.1", "port": index}
                        for index in (1, 2, 3)
                    ],
                })
                self.servers.append(build_party_server(config))
            endpoints = tuple(
                MPCPartyEndpoint(index, *server.server_address)
                for index, server in enumerate(self.servers, start=1)
            )
            # 所有监听地址分配完成后、接收请求前固定 peer 地址。
            # Pin all allocated peer addresses before any server accepts requests.
            for server in self.servers:
                server.peer_endpoints = endpoints
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.threads.append(thread)
            for identity in identities:
                cert, key = materials[identity]
                self.role_clients[identity] = tuple(
                    MPCPartyHttpClient(endpoint, tls_config=MPCPartyTLSConfig(
                        cafile=ca, certfile=cert, keyfile=key
                    )) for endpoint in endpoints
                )
            return self
        except BaseException:
            # 只有已启动的服务需要 shutdown，未启动实例直接关闭 socket。
            # Only started servers need shutdown; close unstarted server sockets directly.
            for server in self.servers[len(self.threads):]:
                server.server_close()
            self.servers = self.servers[:len(self.threads)]
            self.__exit__(None, None, None)
            raise

    def sharing(self, identity: str, session_id: str) -> SharingConfig:
        """Build one role's sharing config without mixing certificate identities.

        按角色创建配置，避免数据提交和医保调度混用证书。
        Build role-specific sharing config without mixing certificates.
        """

        return SharingConfig(session_id, 2, 3, 251, self.role_clients[identity])

    def register(self, session_id: str, *, expires_at: int | None = None) -> AuditComputationPlan:
        """登记固定模型；重用业务协议的任务清单。 / Register using the business manifest."""
        plan = self.servers[0].computation_plan
        remote_register_task(
            self.sharing("insurer", session_id),
            expires_at=int(time()) + 3600 if expires_at is None else expires_at,
            model_version=plan.model_version, feature_secret_ids=plan.feature_ids,
        )
        return plan


def request_json(client: MPCPartyHttpClient, method: str, path: str,
                 payload: dict | None = None) -> tuple[int, dict]:
    """检查原始状态码，仍复用客户端 TLS。 / Inspect status using the client's TLS."""
    connection = client._connection()
    try:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()
