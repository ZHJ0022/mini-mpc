from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from mini_mpc.sharing.share import Share
from mini_mpc.network.task_registry import TaskManifest


@dataclass(frozen=True)
class MPCPartyEndpoint:
    """Address of one MPC party service.

    一个 MPC party 服务的地址；默认使用 HTTPS，明文 HTTP 需显式指定。
    Address of one MPC party service; HTTPS is the default, and plaintext HTTP
    must be explicit.
    """

    party_id: int
    host: str
    port: int
    scheme: str = "https"


class PartyTransport(Protocol):
    """Transport interface used by application nodes.

    应用节点依赖的传输接口；HTTP、HTTPS/mTLS 或自定义安全信道都应实现它。
    Transport interface used by application nodes; HTTP, HTTPS/mTLS, or a
    custom secure channel can implement it.
    """

    endpoint: MPCPartyEndpoint

    def register_task(self, manifest: TaskManifest) -> None:
        """Register a bounded task with this party.

        向当前 party 登记有限期任务。
        Register a bounded audit task with this party.
        """

    def submit_share(self, session_id: str, secret_id: str, share: Share) -> None:
        """Send one share to the endpoint party.

        向 endpoint 对应的 party 发送一份 share。
        Send one share to the party represented by this endpoint.
        """

    def submit_reshare(self, session_id: str, secret_id: str, share: Share) -> None:
        """Send a party-owned BGW reshare to this peer.

        向当前 peer party 提交 BGW 重分享，和机构特征写入分开。
        Send a BGW reshare separately from data-holder feature submission.
        """

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
        """Submit a public constant as a party-local share.

        将公开常量提交为 party-local share。
        """

    def compute_weighted_sum_share(
        self,
        session_id: str,
        input_secret_ids: Sequence[str],
        weights: Sequence[int],
        output_secret_id: str,
    ) -> Share:
        """Ask the endpoint party to compute one weighted output share.

        请求 endpoint party 计算自己的加权 output share。
        Ask the endpoint party to compute its weighted output share.
        """

    def compute_weighted_sum(
        self,
        session_id: str,
        input_secret_ids: Sequence[str],
        weights: Sequence[int],
        output_secret_id: str,
    ) -> None:
        """Store a local result without returning its share to the coordinator.

        仅保存本地结果，不把 share 返回给调度方。
        Store the party-local result without exposing it in the response.
        """

    def distribute_multiplication_reshares(
        self,
        session_id: str,
        left_secret_id: str,
        right_secret_id: str,
        output_secret_id: str,
        peer_endpoints: Sequence[MPCPartyEndpoint],
    ) -> None:
        """Ask the endpoint party to distribute BGW product reshares.

        请求 endpoint party 分发 BGW 局部乘积 reshares。
        Ask the endpoint party to distribute BGW local-product reshares.
        """

    def get_share(self, session_id: str, secret_id: str) -> Share:
        """Fetch one stored share from the endpoint party.

        仅测试协议可读取任意 share；正式服务拒绝此方法。
        Fetch an arbitrary share for protocol tests only; standalone services reject it.
        """

    def get_result_share(self, session_id: str) -> Share:
        """Fetch the registered receiver's final risk-level share.

        由服务端校验任务接收者，固定读取最终等级 share。
        Have the server verify the task receiver and return only the final level share.
        """

    def health(self) -> Mapping[str, object]:
        """Check the endpoint party status.

        检查 endpoint party 状态。
        """
