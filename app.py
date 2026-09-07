"""
FastAPI app wiring the WhatsAppMpesaAgent into two webhook surfaces:

  - Meta WhatsApp Cloud API webhook  (inbound chat messages)
  - Safaricom Daraja result/timeout callbacks (async B2C/B2B/STK outcomes)

Run:
    uvicorn app:app --host 0.0.0.0 --port 8000

Then expose it publicly (e.g. `ngrok http 8000`) and:
  - Register PUBLIC_BASE_URL + /webhooks/whatsapp with WHATSAPP_VERIFY_TOKEN
    in Meta's App Dashboard -> WhatsApp -> Configuration -> Webhook.
  - Safaricom callback URLs are sent per-request (ResultURL/QueueTimeOutURL),
    no dashboard registration needed — just make sure PUBLIC_BASE_URL is set.
"""
import hmac
import hashlib
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse

from jarviscore.integrations.fastapi import JarvisLifespan

import config
from agent import WhatsAppMpesaAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_mpesa_app")

agent = WhatsAppMpesaAgent()

app = FastAPI(
    title="WhatsApp <-> M-Pesa Agent",
    lifespan=JarvisLifespan(agent),
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── WhatsApp Cloud API webhook ──────────────────────────────────────────────

@app.get("/webhooks/whatsapp")
async def whatsapp_verify(request: Request):
    """Meta calls this once, at registration time, to verify ownership."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.whatsapp_verify_token():
        return PlainTextResponse(challenge, status_code=200)
    return PlainTextResponse("verification failed", status_code=403)


def _verify_whatsapp_signature(raw_body: bytes, signature_header: str) -> bool:
    secret = config.whatsapp_app_secret()
    if not secret:
        return True  # not configured — skip (fine for local sandbox testing only)
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


@app.post("/webhooks/whatsapp")
async def whatsapp_receive(request: Request):
    raw_body = await request.body()
    if not _verify_whatsapp_signature(raw_body, request.headers.get("X-Hub-Signature-256", "")):
        return Response(status_code=403)

    body = await request.json()
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                sender = message.get("from")
                text = (message.get("text") or {}).get("body", "")
                if not sender:
                    continue
                reply = agent.handle_incoming_message(sender, text)
                if reply:
                    agent.send_whatsapp_text(sender, reply)

    # Always 200 quickly — Meta retries/disables webhooks that time out or error.
    return JSONResponse({"status": "received"})


# ── Safaricom Daraja B2C / B2B result + timeout callbacks ───────────────────

@app.post("/webhooks/mpesa/result")
async def mpesa_result(request: Request):
    body = await request.json()
    logger.info("M-Pesa result callback: %s", body)
    agent.handle_mpesa_result(body)
    # Safaricom expects this exact acknowledgement shape.
    return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@app.post("/webhooks/mpesa/timeout")
async def mpesa_timeout(request: Request):
    body = await request.json()
    logger.warning("M-Pesa timeout callback: %s", body)
    return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=config.PORT, reload=True)
