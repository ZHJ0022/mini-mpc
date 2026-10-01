from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import hexdigits

from mini_mpc.sharing.shamir import validate_sharing_parameters
from mini_mpc.network.authorization import DATA_ROLES, party_id_from_identity
from mini_mpc.network.transport import MPCPartyEndpoint
from mini_mpc.network.tls import MPCPartyTLSConfig


@dataclass(frozen=True)
class PartyServiceConfig:
    """Configuration for one standalone MPC party service.

    单个独立 MPC party 服务的配置。

    路径按配置文件所在目录解析，便于部署时把 config 和证书目录一起移动。
    Paths are resolved relative to the config file, so config and certificate
    directories can move together during deployment.
    """

    party_id: int
    host: str
    port: int
    tls: MPCPartyTLSConfig
    peer_tls: MPCPartyTLSConfig
    client_fingerprints: Mapping[str, str]
    # 固定的三方 HTTPS 地址用于 party-to-party BGW 重分享。
    # Pinned HTTPS destinations for party-to-party BGW reshares.
    peer_endpoints: tuple[MPCPartyEndpoint, ...]

    def __post_init__(self) -> None:
        """Validate service configuration before opening a socket.

        打开监听端口前校验服务配置。
        """

        if type(self.party_id) is not int or type(self.port) is not int:
            raise TypeError("party_id and port must be integers")
        if self.party_id not in (1, 2, 3):
            raise ValueError("party_id must be between 1 and 3")
        if not self.host:
            raise ValueError("host must not be empty")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.tls.certfile is None or self.tls.keyfile is None:
            raise ValueError("tls.certfile and tls.keyfile are required")
        if not self.tls.require_client_cert or self.tls.cafile is None:
            raise ValueError("party service requires mTLS and a trusted client CA")
        if not self.peer_tls.verify or self.peer_tls.certfile is None or self.peer_tls.keyfile is None:
            raise ValueError("party peers require verified mTLS client credentials")
        if not self.client_fingerprints:
            raise ValueError("client_fingerprints must map trusted nodes to certificates")
        # 此服务当前运行固定三方模型；地址必须完整且只能使用 HTTPS。
        # This service runs the fixed three-party model; peers must be complete and HTTPS.
        if {endpoint.party_id for endpoint in self.peer_endpoints} != {1, 2, 3} or len(self.peer_endpoints) != 3:
            raise ValueError("peer_endpoints must cover party ids 1..3")
        if any(endpoint.scheme != "https" or not endpoint.host or not 1 <= endpoint.port <= 65535 for endpoint in self.peer_endpoints):
            raise ValueError("peer_endpoints must be valid HTTPS addresses")
        fingerprints = set()
        for identity, fingerprint in self.client_fingerprints.items():
            if identity not in DATA_ROLES and party_id_from_identity(identity) is None:
                raise ValueError(f"unknown node identity: {identity}")
            if len(fingerprint) != 64 or any(char not in hexdigits for char in fingerprint):
                raise ValueError("client fingerprint must be a SHA-256 hex digest")
            if fingerprint.lower() in fingerprints:
                raise ValueError("one certificate cannot identify multiple nodes")
            fingerprints.add(fingerprint.lower())


@dataclass(frozen=True)
class RemoteClientConfig:
    """Public MPC party endpoints and protocol settings for data nodes.

    各机构节点共用协议参数，但各自独立持有本方数据库凭据。
    Nodes share MPC parameters while keeping database credentials separate.
    """

    endpoints: tuple[MPCPartyEndpoint, ...]
    tls: MPCPartyTLSConfig
    threshold: int
    num_parties: int
    modulus: int

    def __post_init__(self) -> None:
        validate_sharing_parameters(self.threshold, self.num_parties, self.modulus)
        ids = {endpoint.party_id for endpoint in self.endpoints}
        if len(self.endpoints) != self.num_parties or ids != set(range(1, self.num_parties + 1)):
            raise ValueError("endpoints must cover party ids 1..num_parties")
        for endpoint in self.endpoints:
            if not endpoint.host or not 1 <= endpoint.port <= 65535:
                raise ValueError("party endpoint host or port is invalid")
            if endpoint.scheme != "https":
                raise ValueError("remote client config requires HTTPS endpoints")
        if not self.tls.verify or self.tls.certfile is None or self.tls.keyfile is None:
            raise ValueError("remote client config requires verified mTLS credentials")


def load_remote_client_config(path: str | Path) -> RemoteClientConfig:
    """Load party endpoints and TLS trust from a deployment JSON file.

    按配置文件目录解析 CA 路径，避免在源码中写入证书或私钥。
    Resolve CA paths relative to config, keeping certificate material outside code.
    """

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("remote client config must be a JSON object")
    endpoints_payload = payload.get("endpoints")
    if not isinstance(endpoints_payload, list):
        raise TypeError("endpoints must be a list")
    endpoints = []
    for item in endpoints_payload:
        if not isinstance(item, dict):
            raise TypeError("each endpoint must be an object")
        endpoints.append(MPCPartyEndpoint(
            party_id=_required_int(item, "party_id"),
            host=_optional_str(item, "host", "127.0.0.1"),
            port=_required_int(item, "port"),
            scheme=_optional_str(item, "scheme", "https"),
        ))
    return RemoteClientConfig(
        endpoints=tuple(endpoints),
        tls=_tls_config_from_mapping(_optional_mapping(payload, "tls"), config_path.parent),
        threshold=_required_int(payload, "threshold"),
        num_parties=_required_int(payload, "num_parties"),
        modulus=_required_int(payload, "modulus"),
    )


def load_party_service_config(path: str | Path) -> PartyServiceConfig:
    """Load one party-service JSON config.

    加载一个 party-service JSON 配置。
    """

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("party service config must be a JSON object")
    return party_service_config_from_dict(payload, base_dir=config_path.parent)


def party_service_config_from_dict(
    payload: Mapping[str, object],
    *,
    base_dir: str | Path = ".",
) -> PartyServiceConfig:
    """Build a service config from a parsed JSON object.

    从已解析的 JSON object 构造服务配置。
    """

    base_path = Path(base_dir)
    tls_payload = _optional_mapping(payload, "tls")
    peer_tls_payload = _optional_mapping(payload, "peer_tls") or tls_payload
    return PartyServiceConfig(
        party_id=_required_int(payload, "party_id"),
        host=_optional_str(payload, "host", "127.0.0.1"),
        port=_optional_int(payload, "port", 0),
        tls=_tls_config_from_mapping(tls_payload, base_path),
        peer_tls=_tls_config_from_mapping(peer_tls_payload, base_path),
        client_fingerprints=_client_fingerprints(payload),
        peer_endpoints=_peer_endpoints(payload),
    )


def _peer_endpoints(payload: Mapping[str, object]) -> tuple[MPCPartyEndpoint, ...]:
    """Read trusted BGW destinations from local deployment config.

    从本地部署配置读取可信 BGW 目的地址，而非信任请求提供的地址。
    Read trusted BGW destinations from deployment config, not a request.
    """

    values = payload.get("peer_endpoints")
    if not isinstance(values, list):
        raise TypeError("peer_endpoints must be a list")
    endpoints = []
    for item in values:
        if not isinstance(item, dict):
            raise TypeError("each peer endpoint must be an object")
        endpoints.append(MPCPartyEndpoint(
            party_id=_required_int(item, "party_id"),
            host=_optional_str(item, "host", "127.0.0.1"),
            port=_required_int(item, "port"),
            scheme=_optional_str(item, "scheme", "https"),
        ))
    return tuple(endpoints)


def _tls_config_from_mapping(
    payload: Mapping[str, object] | None,
    base_dir: Path,
) -> MPCPartyTLSConfig:
    if payload is None:
        raise ValueError("tls config is required for party service")
    return MPCPartyTLSConfig(
        certfile=_optional_path(payload, "certfile", base_dir),
        keyfile=_optional_path(payload, "keyfile", base_dir),
        cafile=_optional_path(payload, "cafile", base_dir),
        verify=_optional_bool(payload, "verify", True),
        check_hostname=_optional_bool(payload, "check_hostname", True),
        require_client_cert=_optional_bool(payload, "require_client_cert", False),
    )


def _client_fingerprints(payload: Mapping[str, object]) -> Mapping[str, str]:
    """Read certificate pins without treating request-provided roles as identities.

    从部署配置读取证书指纹；请求中的角色字段不能决定身份。
    Read certificate pins from deployment config, never from a request body.
    """

    value = payload.get("client_fingerprints")
    if not isinstance(value, dict) or not all(
        isinstance(identity, str) and isinstance(fingerprint, str)
        for identity, fingerprint in value.items()
    ):
        raise TypeError("client_fingerprints must be a role-to-fingerprint object")
    return {identity: fingerprint.lower() for identity, fingerprint in value.items()}


def _optional_path(
    payload: Mapping[str, object],
    field_name: str,
    base_dir: Path,
) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return str(path)


def _required_int(payload: Mapping[str, object], field_name: str) -> int:
    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _optional_int(
    payload: Mapping[str, object],
    field_name: str,
    default: int,
) -> int:
    value = payload.get(field_name, default)
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


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


def _optional_bool(
    payload: Mapping[str, object],
    field_name: str,
    default: bool,
) -> bool:
    value = payload.get(field_name, default)
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _optional_mapping(
    payload: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object] | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be an object")
    return value
