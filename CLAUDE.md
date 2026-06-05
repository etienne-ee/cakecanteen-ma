# CakeCart AI Agent

Shopping assistant for [CakeCart](https://cakecartcopy.electricegg.site/) built on Claude Managed Agents.

## Stack

- **Backend:** FastAPI + uvicorn
- **AI:** Claude Managed Agents (`claude-sonnet-4-6`)
- **E-commerce:** WooCommerce REST API — custom `search_products` tool (no native MCP)
- **Email alerts:** Resend
- **WhatsApp:** Twilio (code present but inactive — see below)

## Store details

| Field | Value |
|-------|-------|
| Store name | CakeCart |
| Store URL | https://cakecartcopy.electricegg.site/ |
| Platform | WooCommerce |

## Running locally

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
venv/bin/python3 -m uvicorn main:app --port 8000
```

First run creates the Claude Managed Agent and prints `AGENT_ID` and `ENVIRONMENT_ID`, which are saved automatically to `.env`. Subsequent runs reuse them.

## Critical notes

- **Clear `AGENT_ID` and `ENVIRONMENT_ID` in `.env` whenever you change the system prompt.**
  The agent is created once and cached — it will not pick up prompt changes unless you delete those two values and let the server recreate the agent on next start.

- **Hard-refresh the browser** after restarting the server. The frontend caches the session ID, which becomes invalid after a restart.

- **If using ngrok or a tunnel:** the public URL changes each session. Update your chat widget embed or any webhook URLs accordingly.

## WhatsApp (Twilio) — currently inactive

The full WhatsApp/Twilio implementation is in `main.py` but gated behind `WHATSAPP_ENABLED = False` (line ~37). To activate:

1. Fill in `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` in `.env`
2. Set `WHATSAPP_ENABLED = True` in `main.py`
3. Point your Twilio webhook to `https://<your-server>/whatsapp`
4. Restart the server

## WooCommerce product search

Products are searched via `GET /wp-json/wc/v3/products?search=...`. The agent calls the custom `search_products` tool — the server executes the WooCommerce API call synchronously within the stream thread and returns results directly to the agent. Product IDs are integers (not Shopify-style GIDs).

## Order creation

Orders are submitted via `POST /wp-json/wc/v3/orders` with `payment_method: "cod"`. The WooCommerce order number is returned in the `order_confirmed` SSE event. Consumer key and secret are passed as query params (standard WooCommerce REST auth).
