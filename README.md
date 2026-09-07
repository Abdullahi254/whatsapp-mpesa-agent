# WhatsApp <-> M-Pesa Agent

Chat with a WhatsApp bot (yourself, or anyone you allow) to trigger real
M-Pesa money movement — pay a Till, pay a PayBill, or send money straight to
someone's phone — via Safaricom Daraja. Built on
[jarviscore-framework](https://github.com/Prescott-Data/jarviscore-framework).

**This app moves real money in production. Read the Safety section before
going beyond sandbox.**

## How it works

```
You (WhatsApp) ──message──▶ Meta Cloud API ──webhook POST──▶ /webhooks/whatsapp
                                                                     │
                                                     parse command, ask "yes"?
                                                                     │
                                                        you reply "yes" ─────▶ Safaricom Daraja
                                                                                 (STK/B2C/B2B)
                                                                     │
                                            Safaricom POSTs the result async ──▶ /webhooks/mpesa/result
                                                                     │
                                                        WhatsApp confirmation ◀───┘
```

Every payment command requires an explicit "yes" reply before anything is
sent to Safaricom (human-in-the-loop). Do not remove this gate.

## What you need

**WhatsApp Cloud API (Meta for Developers)**
1. Create an app at https://developers.facebook.com/ with the WhatsApp product added.
2. Grab `phone_number_id` and `whatsapp_business_account_id` from WhatsApp -> API Setup.
3. Generate a **permanent** access token via a System User (Business Settings -> System Users)
   — temporary tokens from the quickstart page expire in 24h.
4. Pick your own `WHATSAPP_VERIFY_TOKEN` string (any random value).

**Safaricom Daraja**
1. Create an app at https://developer.safaricom.co.ke/ → get Consumer Key/Secret.
2. Sandbox testing needs no extra setup for STK Push (`MPESA_BUSINESS_SHORT_CODE=174379`
   and the public sandbox passkey are already in `.env.example`).
3. B2C / B2B (send money) additionally require:
   - `MPESA_INITIATOR_NAME` — set up in the M-Pesa Org business portal with B2C/B2B initiator rights.
   - `MPESA_SECURITY_CREDENTIAL` — your initiator password, encrypted with Safaricom's
     public certificate (RSA, PKCS1v15) and base64-encoded. This is normally generated
     **once, offline** — e.g.:
     ```bash
     openssl rsautl -encrypt -certin -inkey SandboxCertificate.cer -pkcs \
       | base64 <<< "YourInitiatorPassword"
     ```
     Get the sandbox/production certificate from the Daraja docs. Do not commit
     the encrypted credential to source control — put it only in `.env` / your
     credential vault.

**Infra**
- A public HTTPS URL (both providers push data to you): `ngrok http 8000` works for local dev.
- Nothing else is required to start — JarvisCore auto-detects available infra
  (Redis/P2P) and runs standalone if none is present.

## Setup

```bash
cd whatsapp-mpesa-agent
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt   # installs jarviscore-framework[web] from PyPI

cp .env.example .env
# fill in .env with your WhatsApp + Daraja sandbox credentials

ngrok http 8000
# copy the https://xxxx.ngrok-free.app URL into PUBLIC_BASE_URL in .env

uvicorn app:app --host 0.0.0.0 --port 8000
```

Then, in the Meta App Dashboard -> WhatsApp -> Configuration -> Webhook:
- Callback URL: `https://<your-ngrok-domain>/webhooks/whatsapp`
- Verify Token: same value as `WHATSAPP_VERIFY_TOKEN` in `.env`
- Subscribe to the `messages` field.

No dashboard registration is needed for Safaricom — `ResultURL`/`QueueTimeOutURL`
are sent per-request, pointing at `/webhooks/mpesa/result` and `/webhooks/mpesa/timeout`.

## Try it

Message your WhatsApp test number:

```
help
pay 10 to till 123456
yes
```

You'll get an STK-style acceptance, then a follow-up confirmation once
Safaricom posts the async result.

## Project layout

- `app.py` — FastAPI app, webhook routes, `JarvisLifespan` wiring
- `agent.py` — `WhatsAppMpesaAgent(CustomAgent)`: command parsing, confirmation gate, Safaricom dispatch
- `config.py` — env-var-backed settings (swap for Nexus in production, see below)
- `pending_store.py` — in-memory conversation/transaction state (swap for Redis in production)
- `mpesa_atoms/` — B2C (`mpesa_create_payout`) and B2B (`mpesa_send_to_till`,
  `mpesa_send_to_paybill`) atoms for the Daraja APIs used here. These live in
  *this* project rather than in `jarviscore-framework` itself: they're plain
  functions (`auth_info: dict -> dict`), so nothing about calling them
  requires living inside the framework's package, and keeping them here means
  no editable/local install of the framework, no CLA, and you can tweak them
  freely for your own business rules. The framework ships the matching STK
  Push (pay-in) atom as `safaricom_mpesa_create_order` — see
  `jarviscore/integrations/atoms/safaricom_mpesa/` in jarviscore-framework —
  and reusing the framework's PyPI release is enough for this app.

## Safety notes before production

- **Allow-list senders.** `ALLOWED_WHATSAPP_SENDERS` in `.env` restricts who can
  trigger payments. Leaving it blank means *any* WhatsApp number that messages
  your bot can move money — don't do this in production.
- **Verify webhook signatures.** Set `WHATSAPP_APP_SECRET` so inbound requests
  are HMAC-verified (`X-Hub-Signature-256`) — otherwise anyone who discovers
  your webhook URL can inject fake messages.
- **Use Nexus for credentials in production**, not raw `.env` values. See
  `config.py` — it's written as a simple starter; swap it for a
  `CustomAgent` with `requires_auth = True` reading `self._auth_manager`
  (Nexus-injected, encrypted) so secrets never sit in plaintext env vars.
  https://jarviscore.developers.prescottdata.io/concepts/architecture/#nexus
- **Durable state.** `pending_store.py` is in-memory (a dict) — a process
  restart mid-payment loses the pending confirmation and the callback lookup.
  Swap it for Redis (JarvisCore already supports `REDIS_URL` for durable
  workflow state) before relying on this for anything real.
- **Go-live approval.** Safaricom requires a go-live review before production
  B2C/B2B/STK traffic on your own Paybill/Till (not sandbox).
