from __future__ import annotations

import ssl
from dataclasses import dataclass


@dataclass(frozen=True)
class MPCPartyTLSConfig:
    """TLS material for HTTPS party transport.

    MPC party HTTPS 传输使用的 TLS 材料。
    TLS material used by the MPC party HTTPS transport.

    生产环境应从部署配置传入真实证书、私钥和可信 CA；不要把生产私钥写入代码库。
    Production should inject real certificates, private keys, and trusted CAs
    from deployment config; never commit production private keys.
    """

    certfile: str | None = None
    keyfile: str | None = None
    cafile: str | None = None
    verify: bool = True
    check_hostname: bool = True
    require_client_cert: bool = False

    def client_context(self) -> ssl.SSLContext:
        """Build the client TLS context.

        构造客户端 TLS context；真实部署应保持证书校验开启并配置可信 CA。
        Build the client TLS context; real deployments should keep
        verification enabled and provide a trusted CA.
        """

        if (self.certfile is None) != (self.keyfile is None):
            raise ValueError("client certfile and keyfile must be configured together")

        if self.verify:
            context = ssl.create_default_context(cafile=self.cafile)
            context.check_hostname = self.check_hostname
        else:
            # 仅用于本地调试或受控测试；部署配置必须开启证书校验。
            # Only controlled tests may disable server-certificate verification.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if self.certfile is not None:
            # 同一客户端证书用于机构到 party、party 到 peer 的 mTLS。
            # The caller's certificate authenticates it to party and peer services.
            context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        return context

    def server_context(self) -> ssl.SSLContext:
        """Build the server TLS context.

        构造服务端 TLS context；真实部署的证书路径应来自配置或 secret 管理。
        Build the server TLS context; real certificate paths should come from
        config or secret management.
        """

        if self.certfile is None or self.keyfile is None:
            raise ValueError("HTTPS server requires certfile and keyfile")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        if self.require_client_cert:
            if self.cafile is None:
                raise ValueError("mTLS server requires a trusted client CA")
            context.load_verify_locations(cafile=self.cafile)
            context.verify_mode = ssl.CERT_REQUIRED
        return context
