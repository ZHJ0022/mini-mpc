"""Structured application events without request bodies or secret values.

结构化应用事件只记录白名单元数据，不记录请求体或秘密值。
Structured application events contain allowlisted metadata, never secret values.
"""

import json
import logging
from datetime import datetime, timezone


LOGGER = logging.getLogger("mini_mpc.security")


def record_security_event(
    party_id: int,
    caller_identity: str | None,
    operation: str,
    decision: str,
    *,
    session_id: str | None = None,
    error_code: str | None = None,
) -> None:
    """Record only explicit public metadata; never serialize an exception.

    只序列化显式元数据，不接受 share、请求体或原始异常。
    Serialize explicit metadata only, excluding shares, bodies and exceptions.
    """

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "party_id": party_id,
        "session_id": session_id,
        "caller_identity": caller_identity,
        "operation": operation,
        "decision": decision,
        "error_code": error_code,
    }
    # JSON 转义换行等字符，保持每条事件只有一行。
    # JSON escapes control characters so each event occupies one line.
    LOGGER.info(json.dumps(event, ensure_ascii=True))
