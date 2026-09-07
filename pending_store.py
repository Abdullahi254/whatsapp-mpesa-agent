"""
In-memory conversation + transaction state for the WhatsApp <-> M-Pesa demo.

This is intentionally simple (a dict) so the example runs with zero extra
infra. It is NOT durable: a process restart loses pending confirmations and
in-flight payment lookups. For production, swap this for Redis — which
JarvisCore already uses for durable workflow state (`REDIS_URL`) — so a
`kill -9` mid-payment doesn't strand a callback with nowhere to go.
"""
import time
from typing import Optional, Dict, Any

_PENDING_CONFIRMATION: Dict[str, Dict[str, Any]] = {}   # whatsapp_number -> pending action
_INFLIGHT_BY_CONVERSATION: Dict[str, Dict[str, Any]] = {}  # ConversationID/OriginatorConversationID -> context

_PENDING_TTL_SECONDS = 5 * 60


def set_pending_confirmation(whatsapp_number: str, action: dict) -> None:
    action = dict(action)
    action["_created_at"] = time.time()
    _PENDING_CONFIRMATION[whatsapp_number] = action


def pop_pending_confirmation(whatsapp_number: str) -> Optional[dict]:
    action = _PENDING_CONFIRMATION.pop(whatsapp_number, None)
    if not action:
        return None
    if time.time() - action.get("_created_at", 0) > _PENDING_TTL_SECONDS:
        return None  # expired — treat as if nothing was pending
    return action


def register_inflight(conversation_id: str, context: dict) -> None:
    if not conversation_id:
        return
    context = dict(context)
    context["_created_at"] = time.time()
    _INFLIGHT_BY_CONVERSATION[conversation_id] = context


def resolve_inflight(conversation_id: str) -> Optional[dict]:
    return _INFLIGHT_BY_CONVERSATION.pop(conversation_id, None)
