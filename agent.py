"""
WhatsAppMpesaAgent — a CustomAgent that lets you chat with yourself (or anyone
whitelisted) on WhatsApp to trigger real M-Pesa money movement via Safaricom
Daraja, using the safaricom_mpesa_* atoms shipped in jarviscore-framework.

Supported chat commands (case-insensitive):
    pay <amount> to till <till_number>
    pay <amount> to paybill <paybill_number> ref <account_reference>
    send <amount> to <phone_number>        (B2C payout to a customer wallet)
    yes / confirm                          (confirm the last pending action)
    no / cancel                            (cancel the last pending action)
    help                                   (show usage)

Every money-moving command requires an explicit "yes"/"confirm" reply before
anything is sent to Safaricom — this is the human-in-the-loop gate. Do not
remove it; STK Push / B2C / B2B calls move real funds in production.
"""
import re
import logging
from typing import Optional, Tuple

from jarviscore.profiles import CustomAgent

from jarviscore.integrations.atoms.whatsapp_business.whatsapp_business_create_message import (
    whatsapp_business_create_message,
)
from jarviscore.integrations.atoms.safaricom_mpesa.safaricom_mpesa_create_payout import (
    safaricom_mpesa_create_payout,
)
from jarviscore.integrations.atoms.safaricom_mpesa.safaricom_mpesa_send_to_till import (
    safaricom_mpesa_send_to_till,
)
from jarviscore.integrations.atoms.safaricom_mpesa.safaricom_mpesa_send_to_paybill import (
    safaricom_mpesa_send_to_paybill,
)

import config
import pending_store

logger = logging.getLogger("whatsapp_mpesa_agent")

_RE_PAY_TILL = re.compile(r"^pay\s+(\d+(?:\.\d+)?)\s+to\s+till\s+(\d+)$", re.I)
_RE_PAY_PAYBILL = re.compile(r"^pay\s+(\d+(?:\.\d+)?)\s+to\s+paybill\s+(\d+)\s+ref\s+(\S+)$", re.I)
_RE_SEND = re.compile(r"^send\s+(\d+(?:\.\d+)?)\s+to\s+(\+?\d{9,15})$", re.I)
_RE_YES = re.compile(r"^(yes|confirm|y)$", re.I)
_RE_NO = re.compile(r"^(no|cancel|n)$", re.I)
_RE_HELP = re.compile(r"^(help|menu|\?)$", re.I)

_HELP_TEXT = (
    "💬 *Commands*\n"
    "• pay <amount> to till <till_number>\n"
    "• pay <amount> to paybill <paybill_number> ref <account_reference>\n"
    "• send <amount> to <phone_number>\n"
    "Reply *yes* to confirm or *no* to cancel a pending payment."
)


class WhatsAppMpesaAgent(CustomAgent):
    role = "whatsapp_mpesa"
    capabilities = ["messaging", "payments"]

    def send_whatsapp_text(self, to: str, text: str) -> dict:
        return whatsapp_business_create_message(config.whatsapp_auth_info(), to=to, text=text)

    # ── Inbound WhatsApp message handling ──────────────────────────────────

    def handle_incoming_message(self, sender: str, text: str) -> str:
        """Returns the reply text to send back to `sender`. Never raises."""
        text = (text or "").strip()

        if not config.is_sender_allowed(sender):
            logger.warning("Rejected message from non-allow-listed sender %s", sender)
            return "Sorry, this number is not authorized to use this assistant."

        if _RE_HELP.match(text):
            return _HELP_TEXT

        if _RE_YES.match(text):
            return self._confirm_pending(sender)

        if _RE_NO.match(text):
            cancelled = pending_store.pop_pending_confirmation(sender)
            return "Cancelled." if cancelled else "There's nothing pending to cancel."

        action, err = self._parse_command(text)
        if err:
            return err
        if action:
            pending_store.set_pending_confirmation(sender, action)
            return f"{action['confirm_prompt']}\nReply *yes* to confirm or *no* to cancel."

        return "Sorry, I didn't understand that. Reply *help* for the list of commands."

    def _parse_command(self, text: str) -> Tuple[Optional[dict], Optional[str]]:
        m = _RE_PAY_TILL.match(text)
        if m:
            amount, till = m.group(1), m.group(2)
            return {
                "kind": "till",
                "amount": amount,
                "till_number": till,
                "confirm_prompt": f"Pay KES {amount} to Till {till}?",
            }, None

        m = _RE_PAY_PAYBILL.match(text)
        if m:
            amount, paybill, ref = m.group(1), m.group(2), m.group(3)
            return {
                "kind": "paybill",
                "amount": amount,
                "paybill_number": paybill,
                "account_reference": ref,
                "confirm_prompt": f"Pay KES {amount} to PayBill {paybill} (ref {ref})?",
            }, None

        m = _RE_SEND.match(text)
        if m:
            amount, phone = m.group(1), m.group(2)
            return {
                "kind": "send",
                "amount": amount,
                "phone_number": phone,
                "confirm_prompt": f"Send KES {amount} to {phone}?",
            }, None

        return None, None

    # ── Confirmation -> Safaricom call ──────────────────────────────────────

    def _confirm_pending(self, sender: str) -> str:
        action = pending_store.pop_pending_confirmation(sender)
        if not action:
            return "There's nothing pending to confirm. Reply *help* for commands."

        auth_info = config.mpesa_auth_info()
        payload_common = {
            "amount": action["amount"],
            "result_url": config.mpesa_result_url(),
            "queue_timeout_url": config.mpesa_timeout_url(),
        }

        try:
            if action["kind"] == "till":
                result = safaricom_mpesa_send_to_till(auth_info, {
                    **payload_common,
                    "till_number": action["till_number"],
                    "remarks": "WhatsApp payment",
                })
                description = f"KES {action['amount']} to Till {action['till_number']}"
            elif action["kind"] == "paybill":
                result = safaricom_mpesa_send_to_paybill(auth_info, {
                    **payload_common,
                    "paybill_number": action["paybill_number"],
                    "account_reference": action["account_reference"],
                    "remarks": "WhatsApp payment",
                })
                description = f"KES {action['amount']} to PayBill {action['paybill_number']}"
            elif action["kind"] == "send":
                result = safaricom_mpesa_create_payout(auth_info, {
                    **payload_common,
                    "phone_number": action["phone_number"],
                    "remarks": "WhatsApp payout",
                })
                description = f"KES {action['amount']} to {action['phone_number']}"
            else:
                return "Internal error: unknown action kind."
        except Exception as exc:  # noqa: BLE001 — never let a webhook handler crash on a network error
            logger.exception("Safaricom call failed")
            return f"Sorry, something went wrong contacting Safaricom: {exc}"

        if result.get("status", 500) >= 400:
            return f"❌ Safaricom rejected the request: {result.get('message')}"

        record = (result.get("records") or [{}])[0]
        conversation_id = record.get("ConversationID") or record.get("OriginatorConversationID")
        if conversation_id:
            pending_store.register_inflight(conversation_id, {
                "sender": sender,
                "description": description,
            })
        return f"✅ Request accepted by Safaricom: {description}. You'll get a confirmation shortly."

    # ── Safaricom result callback -> WhatsApp confirmation ─────────────────

    def handle_mpesa_result(self, body: dict) -> None:
        """Called from the /webhooks/mpesa/result route. Sends a WhatsApp
        follow-up message to whoever triggered the original payment."""
        result = (body or {}).get("Result", {})
        conversation_id = result.get("ConversationID") or result.get("OriginatorConversationID")
        if not conversation_id:
            logger.warning("M-Pesa result callback missing ConversationID: %s", body)
            return

        context = pending_store.resolve_inflight(conversation_id)
        if not context:
            logger.warning("No pending transaction found for ConversationID %s", conversation_id)
            return

        result_code = result.get("ResultCode")
        result_desc = result.get("ResultDesc", "")
        if str(result_code) == "0":
            text = f"✅ Payment confirmed: {context['description']}.\n{result_desc}"
        else:
            text = f"❌ Payment failed: {context['description']}.\n{result_desc}"

        self.send_whatsapp_text(context["sender"], text)
