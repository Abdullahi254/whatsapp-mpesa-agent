import requests
from typing import Any, Dict, List, Optional

def mpesa_send_to_paybill(auth_info: dict, payload: Dict[str, Any], timeout: int = 30, verify_ssl: bool = True, base_url: str = None) -> dict:
    """Business PayBill (B2B): pay another business's PayBill shortcode from your own business shortcode.
    Official: https://developer.safaricom.co.ke/apis/BusinessPayBill

    payload:
      amount (required), paybill_number / PartyB (required, receiving PayBill shortcode),
      account_reference (required, the receiving paybill's account number), remarks,
      result_url / ResultURL (required), queue_timeout_url / QueueTimeOutURL (required),
      originator_conversation_id (optional)

    auth_info: business_short_code (PartyA, sender), initiator_name, security_credential
      (pre-encrypted with Safaricom's public certificate — see official docs), or access_token.
    """
    try:
        if not isinstance(payload, dict) or not payload:
            return _mp_provision({}, 400, "payload is required")
        body_payload, err = _mp_b2b_fields(auth_info, payload, command_id="BusinessPayBill", receiver_identifier_type="4")
        if err:
            return _mp_provision({}, 400, err)
        if not body_payload.get("AccountReference"):
            return _mp_provision({}, 400, "account_reference is required for a PayBill payment")
        resp, body, status, msg = _mp_post("/mpesa/b2b/v1/paymentrequest", base_url, auth_info, body_payload, timeout, verify_ssl)
        if status >= 400:
            return _mp_provision(body if isinstance(body, dict) else {}, status, msg)
        return _mp_provision(body if isinstance(body, dict) else {}, status, "ok")
    except Exception as e:
        return _mp_provision({}, 500, str(e))


# Safaricom M-Pesa Daraja API — Official docs: https://developer.safaricom.co.ke/


def _mp_root(base_url, auth_info):
    auth_info = auth_info or {}
    root = (base_url or auth_info.get("mpesa_url") or auth_info.get("daraja_url") or auth_info.get("base_url") or "https://sandbox.safaricom.co.ke").strip().rstrip("/")
    for suffix in ("/oauth/v1/generate", "/mpesa/stkpush/v1/processrequest", "/mpesa/b2c/v1/paymentrequest", "/mpesa/b2b/v1/paymentrequest"):
        if suffix in root:
            root = root.split(suffix)[0]
    return root, None


def _mp_uuid():
    import uuid
    return str(uuid.uuid4())


def _mp_oauth(base_url, auth_info, timeout, verify_ssl):
    auth_info = auth_info or {}
    token = auth_info.get("access_token")
    if token:
        return str(token).strip(), None
    key = auth_info.get("username")
    secret = auth_info.get("password")
    if not key or not secret:
        return None, "auth_info requires username and password (or access_token)"
    import base64
    root, _ = _mp_root(base_url, auth_info)
    creds = base64.b64encode(f"{key}:{secret}".encode("utf-8")).decode("ascii")
    resp = requests.get(
        root + "/oauth/v1/generate",
        params={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
        timeout=timeout,
        verify=verify_ssl,
    )
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    if resp.status_code >= 400 or not body.get("access_token"):
        return None, (body.get("errorMessage") or body.get("error") or resp.text or "oauth failed")[:1000]
    return str(body.get("access_token")).strip(), None


def _mp_security_credential(auth_info):
    """Resolve the SecurityCredential. Safaricom requires the Initiator password to be
    encrypted with their environment public certificate (RSA PKCS1v15, base64-encoded).
    That encryption is normally done once, offline, per https://developer.safaricom.co.ke/ —
    this atom accepts the already-encrypted value and does not perform certificate encryption
    itself (avoids adding a crypto dependency just for this)."""
    auth_info = auth_info or {}
    cred = auth_info.get("security_credential")
    if not cred:
        return None, "auth_info.security_credential is required (initiator password encrypted with Safaricom's public certificate)"
    return str(cred), None


def _mp_dataset(records, status, msg):
    recs = records if isinstance(records, list) else []
    return {"records": recs, "data_count": len(recs), "status": status, "message": msg}


def _mp_provision(data, status, msg, fallback_id=None):
    obj = data if isinstance(data, dict) else {}
    pid = obj.get("ConversationID") or obj.get("OriginatorConversationID") or fallback_id
    ids = [pid] if pid not in (None, "") else []
    rec = obj if obj else ({"ConversationID": pid} if ids else {})
    return {"records": [rec] if rec else [], "data_count": 1 if rec else 0, "status": status, "message": msg, "provision_ids": ids}


def _mp_err(resp, body=None):
    if isinstance(body, dict):
        err = body.get("errorMessage") or body.get("error")
        if not err:
            err = body.get("ResponseDescription") or body.get("ResultDesc")
        if err:
            return str(err)[:1000]
    return (resp.text if resp is not None else "request failed")[:1000]


def _mp_post(path, base_url, auth_info, json_body, timeout, verify_ssl):
    token, err = _mp_oauth(base_url, auth_info, timeout, verify_ssl)
    if err:
        return None, None, 401, err
    root, _ = _mp_root(base_url, auth_info)
    resp = requests.post(
        root + path,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        json=json_body,
        timeout=timeout,
        verify=verify_ssl,
    )
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    if resp.status_code >= 400:
        return resp, body, resp.status_code, _mp_err(resp, body)
    if isinstance(body, dict) and str(body.get("ResponseCode", "0")) not in ("0", "0.0"):
        return resp, body, 400, _mp_err(resp, body)
    return resp, body, resp.status_code, "ok"


def _mp_b2b_fields(auth_info, payload, command_id, receiver_identifier_type):
    """Shared B2B (Business Buy Goods / Business PayBill) field builder.
    Official identifier types: 1=MSISDN, 2=Till Number, 4=Organization shortcode."""
    auth_info = auth_info or {}
    payload = payload if isinstance(payload, dict) else {}
    shortcode = payload.get("PartyA") or auth_info.get("business_short_code") or auth_info.get("shortcode")
    initiator = payload.get("Initiator") or payload.get("InitiatorName") or auth_info.get("initiator_name")
    if not shortcode:
        return None, "business_short_code (PartyA, your sending shortcode) is required"
    if not initiator:
        return None, "auth_info.initiator_name is required"
    cred, err = _mp_security_credential(auth_info)
    if err:
        return None, err
    amount = int(payload.get("Amount") or payload.get("amount") or 0)
    if amount <= 0:
        return None, "amount is required"
    receiver = str(payload.get("PartyB") or payload.get("till_number") or payload.get("paybill_number") or payload.get("receiver") or "").strip()
    if not receiver:
        return None, "receiving till_number/paybill_number (PartyB) is required"
    result_url = payload.get("ResultURL") or payload.get("result_url")
    timeout_url = payload.get("QueueTimeOutURL") or payload.get("queue_timeout_url")
    if not result_url or not timeout_url:
        return None, "result_url and queue_timeout_url are required"
    return {
        "Initiator": str(initiator),
        "SecurityCredential": cred,
        "CommandID": command_id,
        "SenderIdentifierType": str(payload.get("SenderIdentifierType") or auth_info.get("sender_identifier_type") or "4"),
        "RecieverIdentifierType": str(payload.get("RecieverIdentifierType") or payload.get("ReceiverIdentifierType") or receiver_identifier_type),
        "Amount": amount,
        "PartyA": str(shortcode),
        "PartyB": receiver,
        "AccountReference": str(payload.get("AccountReference") or payload.get("account_reference") or "")[:12],
        "Remarks": str(payload.get("Remarks") or payload.get("remarks") or "Payment")[:100],
        "QueueTimeOutURL": timeout_url,
        "ResultURL": result_url,
        "Occassion": str(payload.get("Occassion") or payload.get("occasion") or "")[:100],
    }, None
