# CakeCart AI Agent

Shopping assistant for [CakeCart](https://cakecanteen.co.za/ ) built on Claude Managed Agents.

## Stack

- **Backend:** FastAPI + uvicorn
- **AI:** Claude Managed Agents (`claude-sonnet-5-5`, effort `medium` — `AGENT_MODEL` / `AGENT_EFFORT` in `main.py`)
- **E-commerce:** WooCommerce REST API, called by MCP tools served from this app at `/mcp` (`search_products`, `search_products_batch`, `get_order`, …)
- **Email alerts:** Resend
- **WhatsApp:** Twilio (active — `WHATSAPP_ENABLED = True` in `main.py`, see below)

## Store details

| Field | Value |
|-------|-------|
| Store name | CakeCart |
| Store URL | https://cakecanteen.co.za/ |
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

- **Prompt changes publish automatically — never clear `AGENT_ID` or `ENVIRONMENT_ID`.**
  On every startup the server rebuilds the system prompt and, if it differs from the live agent's, calls `agents.update()` to publish it as a new version of the same agent (the ID never changes). New sessions always use the latest version; running sessions keep theirs. On Railway this means deploy = publish: the restart after a push publishes the new prompt.

- **On Railway, `AGENT_ID` and `ENVIRONMENT_ID` must live in the service's environment variables.** The container filesystem is ephemeral — the `.env` values saved on first run are lost on redeploy, and without them the server would create a duplicate agent.

- **Set `EMAIL_DRY_RUN=1` in local `.env` before any test run.** `forward_query`, `report_defect` and tool-failure alerts otherwise send a real email on every call. With it on they are logged instead. Never set it on Railway.

- **Hard-refresh the browser** after restarting the server. The frontend caches the session ID, which becomes invalid after a restart.

- **If using ngrok or a tunnel:** the public URL changes each session. Update your chat widget embed or any webhook URLs accordingly.

## WhatsApp (Twilio) — active

The WhatsApp/Twilio implementation in `main.py` is enabled (`WHATSAPP_ENABLED = True`, line ~39). It requires:

1. `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` in `.env`
2. The Twilio webhook pointed to `https://<your-server>/whatsapp`

To deactivate, set `WHATSAPP_ENABLED = False` in `main.py` and restart.

## WooCommerce product search

Products are searched via `GET /wp-json/wc/v3/products?search=...` (with `_fields`, so only the fields the agent uses come back). Two MCP tools share one helper, `_wc_search`:

- `search_products` — one search.
- `search_products_batch` — up to 5 searches run at the same time in one call. Anthropic's runtime runs MCP tool calls one at a time, so several `search_products` calls in a turn wait for each other; one batch call doesn't. Measured 2026-10-26: 2–3 searches together ≈ one search.

Both log a `🔎 … searches in Ns` line. Product IDs are integers (not Shopify-style GIDs). The "🎂 Find a cake" widget button's message is answered from the prompt's category list with no search (see `Planned-Updates/26.10.2026.md`).

## Chat widget

The widget is `static/widget.js`, served from Railway. WordPress holds only the one-line embed in `chat-widget-script.html` (Avada → Options → Advanced → Code Fields → Space before `</body>` — confirm on first edit). `/static` is served `no-cache` + ETag, so **widget changes ship with a push**, the same as agent changes: no re-pasting into WordPress. The avatar is inlined in `widget.js` as a data URI.

## Order creation

Orders are submitted via `POST /wp-json/wc/v3/orders` with `payment_method: "cod"`. The WooCommerce order number is returned in the `order_confirmed` SSE event. Consumer key and secret are passed as query params (standard WooCommerce REST auth).
