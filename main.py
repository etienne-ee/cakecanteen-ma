import os
import json
import asyncio
import re
import smtplib
import threading
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import httpx
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, Form, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from pydantic import BaseModel

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
WC_STORE_URL       = os.getenv("WC_STORE_URL", "https://cakecartcopy.electricegg.site/")
WC_CONSUMER_KEY    = os.getenv("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.getenv("WC_CONSUMER_SECRET")
PUBLIC_URL         = os.getenv("PUBLIC_URL", "").rstrip("/")  # e.g. https://web-production-d8a27a.up.railway.app
ALLOWED_ORIGINS    = os.getenv("ALLOWED_ORIGINS", "*").split(",")
STORE_OWNER_EMAIL  = os.getenv("STORE_OWNER_EMAIL")
SMTP_HOST          = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER          = os.getenv("SMTP_USER")
SMTP_PASSWORD      = os.getenv("SMTP_PASSWORD")

TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
WHATSAPP_ENABLED     = False  # Set to True and fill Twilio credentials in .env to activate

if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
if not WC_STORE_URL:
    raise RuntimeError("WC_STORE_URL is not set in .env")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
executor = ThreadPoolExecutor(max_workers=20)

# WhatsApp session store: maps phone number → Managed Agent session ID
# In-memory only — resets on server restart. Swap for Redis/DB in production.
whatsapp_sessions: dict[str, str] = {}

# Per-number lock: prevents concurrent agent calls for the same WhatsApp number.
_whatsapp_locks: dict[str, threading.Lock] = {}
_whatsapp_locks_guard = threading.Lock()

def _get_whatsapp_lock(phone_number: str) -> threading.Lock:
    with _whatsapp_locks_guard:
        if phone_number not in _whatsapp_locks:
            _whatsapp_locks[phone_number] = threading.Lock()
        return _whatsapp_locks[phone_number]

# ── WooCommerce MCP server (mounted into the main FastAPI app at /mcp) ─────────

wc_mcp = FastMCP("WooCommerce MCP")


@wc_mcp.tool()
def search_products(query: str, category_id: int = 0, per_page: int = 10) -> str:
    """Search for published products in the CakeCart WooCommerce store.
    Pass category_id to filter by product category (preferred for type/flavour queries).
    Pass query for keyword search within a category or across all products.
    Returns product IDs, names, prices, images, stock status, and permalinks."""
    per_page = min(per_page, 25)

    params: dict = {
        "per_page": 50,
        "status": "publish",
        "consumer_key": WC_CONSUMER_KEY,
        "consumer_secret": WC_CONSUMER_SECRET,
    }
    if query:
        params["search"] = query
    if category_id:
        params["category"] = str(category_id)

    with httpx.Client(timeout=10) as http:
        resp = http.get(
            f"{WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products",
            params=params,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CakeCartBot/1.0)"},
        )
        resp.raise_for_status()
        products = resp.json()

    # When doing a keyword search without a category, prefer title matches
    if query and not category_id:
        query_words = [w.lower() for w in query.split() if len(w) > 2]

        def _title_score(p: dict) -> int:
            return sum(1 for w in query_words if w in p["name"].lower())

        title_hits = [p for p in products if _title_score(p) > 0]
        products = sorted(title_hits or products, key=_title_score, reverse=True)

    return json.dumps([
        {
            "id": p["id"],
            "title": p["name"],
            "price": f"R {float(p.get('price') or 0):.2f}",
            "url": p.get("permalink", ""),
            "image_url": p["images"][0]["src"] if p.get("images") else None,
            "short_description": p.get("short_description", ""),
            "stock_status": p.get("stock_status", "instock"),
        }
        for p in products[:per_page]
    ])


@wc_mcp.tool()
def report_defect(order_number: str, customer_name: str, issue: str, contact: str) -> str:
    """Report a defective, damaged, or wrong order to the store team by email.
    Call this once you have the customer's order number, their name, a description
    of the issue, and a contact detail (email or phone).
    Returns a confirmation string indicating whether the report was sent."""
    sent = _send_defect_email({
        "order_number": order_number,
        "customer_name": customer_name,
        "issue": issue,
        "contact": contact,
    })
    if sent:
        return f"Report sent successfully for order #{order_number}. The store team has been notified and will follow up with {contact}."
    return "Could not send report — email is not configured on the server. Advise the customer to contact the store directly."


# ─────────────────────────────────────────────────────────────────────────────

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

agent_id: str | None = None
environment_id: str | None = None


def _fetch_wc_categories() -> list[dict]:
    """Fetch all non-empty product categories from WooCommerce at startup."""
    try:
        with httpx.Client(timeout=10) as http:
            resp = http.get(
                f"{WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products/categories",
                params={
                    "per_page": 100,
                    "consumer_key": WC_CONSUMER_KEY,
                    "consumer_secret": WC_CONSUMER_SECRET,
                },
                headers={"User-Agent": "Mozilla/5.0 (compatible; CakeCartBot/1.0)"},
            )
            resp.raise_for_status()
            return [
                {"id": c["id"], "name": c["name"], "count": c["count"]}
                for c in resp.json()
                if c["count"] > 0 and c["name"] != "Uncategorized"
            ]
    except Exception as exc:
        print(f"⚠️  Could not fetch categories: {exc}")
        return []


def _build_system_prompt(categories: list[dict]) -> str:
    cat_lines = "\n".join(
        f'  - {c["name"]} (category_id: {c["id"]}, {c["count"]} products)'
        for c in categories
    )
    categories_section = (
        f"## Product categories\n\nUse these category IDs with search_products:\n\n{cat_lines}\n"
        if cat_lines
        else ""
    )
    return f"""You are a friendly and helpful shopping assistant for CakeCart.

Your job is to help customers:
- Search and discover products using natural language
- Understand product details, pricing, and availability
- Add items to their cart and place orders
- Answer questions about store policies, shipping, and returns

Always be warm, conversational, and focused on finding the right product quickly.
Store URL: {WC_STORE_URL}

{categories_section}
## Search strategy

Always call search_products with per_page set to 10 or less — never exceed 25.

When a customer asks for a specific type or flavour of product, use the matching category_id
from the list above — this is more accurate than a keyword search alone.
Examples:
- "Do you have chocolate cake?" → search_products(category_id=<chocolate category id>)
- "Show me birthday cakes" → search_products(category_id=<birthday category id>)
- "Any vegan options?" → search_products(category_id=<vegan category id>)

If no category clearly matches, fall back to a keyword query:
1. Search with the customer's specific keywords first.
2. If that returns 0 products, try a broader term.
3. Never tell a customer something doesn't exist based on a single failed search — try at least two searches first.

## Product cards

Whenever you list one or more products, append a JSON block at the very end of your message in exactly this format (nothing after the closing fence):

```products
[
  {{
    "title": "Product Name",
    "price": "R 0.00",
    "image_url": "https://...",
    "url": "https://..."
  }}
]
```

Rules:
- Use the exact image_url and permalink from the WooCommerce product data.
- Include every product you mention in the block.
- If a product has no image, omit the image_url field.
- Never fabricate URLs or prices.

## Cart and checkout

When you show a customer a product, note its `id` (integer) from the WooCommerce product data.

Keep a mental note of the customer's cart — what they've asked to add, each item's product_id (integer), and quantity.

When a customer wants to checkout, collect their details in exactly two friendly messages — not one field at a time:

**Message 1** — ask for name and email together in a warm, natural way. Example:
"Sounds great! To get your order on its way, what's your name and email address?"

**Message 2** — once you have those, ask for phone and full delivery address together. Example:
"Perfect! And what's the best number to reach you on, and where should we deliver? (Street address, city, and postal code)"

Once you have all four pieces of info, give a warm summary — list the items, total, and delivery address — and let them know payment is Cash on Delivery (collected at the door, no card needed). Then ask them to confirm.

When the customer confirms, output this block at the very end of your message and nothing after it:

```order
{{
  "customer": {{
    "first_name": "...",
    "last_name": "...",
    "email": "...",
    "phone": "..."
  }},
  "shipping_address": {{
    "address1": "...",
    "city": "...",
    "zip": "...",
    "country_code": "ZA"
  }},
  "line_items": [
    {{"product_id": 123, "title": "...", "quantity": 1}}
  ]
}}
```

Rules:
- Use real product IDs (integers) from the WooCommerce product data — never invent them.
- country_code is always "ZA" for this store.
- Only output the order block once the customer has explicitly confirmed.
- Never ask for payment details — it is always Cash on Delivery.
- Keep the tone friendly and conversational throughout — this should feel like chatting with a helpful person, not filling in a form.

## Defective or damaged orders

If a customer reports receiving a defective, damaged, or wrong item:
1. Apologise sincerely and empathetically.
2. Ask for their order number, a brief description of the problem, and a contact detail (email or phone) — if they haven't already provided them.
3. Once you have all three, call the `report_defect` tool immediately.
4. After the tool returns, tell the customer the report has been submitted and the store team will follow up with them.

Rules:
- Use whatever contact detail the customer has shared (email, phone — whatever is available).
- Only call report_defect once you have the order number, issue description, and a contact detail.
- Do not tell the customer the report was submitted before the tool has returned successfully.
"""

# Create MCP HTTP app once so its lifespan can be wired into FastAPI's lifespan
mcp_http_app = wc_mcp.http_app(path="/")

# ── Lifespan: create agent + environment once on startup ──────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_http_app.lifespan(app):
        global agent_id, environment_id

        saved_agent_id = os.getenv("AGENT_ID")
        saved_env_id   = os.getenv("ENVIRONMENT_ID")

        if saved_agent_id and saved_env_id:
            try:
                client.beta.agents.retrieve(saved_agent_id)
                client.beta.environments.retrieve(saved_env_id)
                agent_id       = saved_agent_id
                environment_id = saved_env_id
                print(f"♻️  Reusing agent:       {agent_id}")
                print(f"♻️  Reusing environment: {environment_id}")
            except Exception:
                print("⚠️  Saved IDs are stale — creating new agent and environment...")
                saved_agent_id = saved_env_id = None

        if not saved_agent_id or not saved_env_id:
            print("🚀 Starting up — creating Claude Managed Agent...")

            categories = _fetch_wc_categories()
            print(f"📂 Loaded {len(categories)} product categories")
            system_prompt = _build_system_prompt(categories)

            agent = client.beta.agents.create(
                name="CakeCart Shopping Assistant",
                model={"id": "claude-sonnet-4-6"},
                system=system_prompt,
                mcp_servers=[
                    {
                        "type": "url",
                        "url": f"{PUBLIC_URL}/mcp/",
                        "name": "woocommerce",
                    },
                ],
                tools=[
                    {"type": "agent_toolset_20260401"},
                    {
                        "type": "mcp_toolset",
                        "mcp_server_name": "woocommerce",
                        "default_config": {"permission_policy": {"type": "always_allow"}},
                    },
                ],
            )
            agent_id = agent.id
            print(f"✅ Agent created: {agent_id}")

            env = client.beta.environments.create(
                name="cakecart-agent-env",
                config={
                    "type": "cloud",
                    "networking": {"type": "unrestricted"},
                },
            )
            environment_id = env.id
            print(f"✅ Environment created: {environment_id}")

            set_key(ENV_FILE, "AGENT_ID", agent_id)
            set_key(ENV_FILE, "ENVIRONMENT_ID", environment_id)
            print("💾 IDs saved to .env")

        print(f"🛒 Connected to WooCommerce store: {WC_STORE_URL}")
        print("🟢 Ready — server is running\n")

        yield

        print("🔴 Shutting down")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="CakeCart AI Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/mcp", mcp_http_app)


# ── Schemas ───────────────────────────────────────────────────────────────────
class SessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    message: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent_id": agent_id,
        "environment_id": environment_id,
        "wc_store": WC_STORE_URL,
    }


@app.post("/api/sessions", response_model=SessionResponse)
def create_session():
    """Create a new chat session for a visitor. Call this once when the chat widget opens."""
    if not agent_id or not environment_id:
        raise HTTPException(503, "Agent not ready yet")

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="CakeCart chat session",
    )
    return {"session_id": session.id}


@app.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest):
    """
    Send a message and stream back the agent response as Server-Sent Events.

    Event types emitted:
      {"type": "text",           "content": "..."}      – text chunk from the agent
      {"type": "tool",           "name": "..."}          – agent is calling a tool
      {"type": "products",       "products": [...]}      – product cards with images
      {"type": "order_confirmed","order_number": "..."}  – order placed successfully
      {"type": "defect_reported","order_number": "..."}  – defect report submitted
      {"type": "done"}                                   – agent finished responding
      {"type": "error",          "message": "..."}       – something went wrong
    """
    if not session_id:
        raise HTTPException(400, "session_id is required")

    async def event_stream():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _run_stream():
            try:
                with client.beta.sessions.events.stream(session_id) as stream:
                    client.beta.sessions.events.send(
                        session_id,
                        events=[{
                            "type": "user.message",
                            "content": [{"type": "text", "text": body.message}],
                        }],
                    )
                    for event in stream:
                        loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        executor.submit(_run_stream)

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if item is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break

            if isinstance(item, Exception):
                yield f"data: {json.dumps({'type': 'error', 'message': str(item)})}\n\n"
                break

            event = item

            if event.type == "agent.message":
                for block in event.content:
                    text = getattr(block, "text", None)
                    if not text:
                        continue

                    # Order block — create WooCommerce order and emit confirmation
                    order_match = re.search(r"```order\s*(\{.*?\})\s*```", text, re.DOTALL)
                    if order_match:
                        clean_text = text[:order_match.start()].rstrip()
                        if clean_text:
                            yield f"data: {json.dumps({'type': 'text', 'content': clean_text})}\n\n"
                        try:
                            order_data = json.loads(order_match.group(1))
                            result = await loop.run_in_executor(
                                executor, lambda: _create_wc_order(order_data)
                            )
                            order_num = result.get("number") or result.get("id")
                            yield f"data: {json.dumps({'type': 'order_confirmed', 'order_number': order_num})}\n\n"
                        except Exception as exc:
                            yield f"data: {json.dumps({'type': 'error', 'message': f'Could not place order: {exc}'})}\n\n"
                        continue

                    # Products block — emit product cards
                    prod_match = re.search(r"```products\s*(\[.*?\])\s*```", text, re.DOTALL)
                    if prod_match:
                        clean_text = text[:prod_match.start()].rstrip()
                        if clean_text:
                            yield f"data: {json.dumps({'type': 'text', 'content': clean_text})}\n\n"
                        try:
                            products = json.loads(prod_match.group(1))
                            yield f"data: {json.dumps({'type': 'products', 'products': products})}\n\n"
                        except json.JSONDecodeError:
                            pass
                    else:
                        yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"

            elif event.type == "agent.tool_use":
                yield f"data: {json.dumps({'type': 'tool', 'name': event.name})}\n\n"

            elif event.type == "session.status_idle":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── WooCommerce helpers ───────────────────────────────────────────────────────

def _search_wc_products(query: str, per_page: int = 10) -> list[dict]:
    """Search published WooCommerce products by keyword."""
    with httpx.Client(timeout=10) as http:
        resp = http.get(
            f"{WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products",
            params={
                "search": query,
                "per_page": min(per_page, 25),
                "consumer_key": WC_CONSUMER_KEY,
                "consumer_secret": WC_CONSUMER_SECRET,
            },
            headers={"User-Agent": "Mozilla/5.0 (compatible; CakeCartBot/1.0)"},
        )
        resp.raise_for_status()
        products = resp.json()
        return [
            {
                "id": p["id"],
                "title": p["name"],
                "price": f"R {float(p.get('price') or 0):.2f}",
                "permalink": p.get("permalink", ""),
                "image_url": p["images"][0]["src"] if p.get("images") else None,
                "short_description": p.get("short_description", ""),
                "stock_status": p.get("stock_status", "instock"),
            }
            for p in products
        ]


def _create_wc_order(order_data: dict) -> dict:
    """Create a WooCommerce order via the REST API with Cash on Delivery payment."""
    customer = order_data["customer"]
    shipping = order_data["shipping_address"]

    billing = {
        "first_name": customer["first_name"],
        "last_name": customer["last_name"],
        "email": customer.get("email", ""),
        "phone": customer.get("phone", ""),
        "address_1": shipping.get("address1", ""),
        "city": shipping.get("city", ""),
        "postcode": shipping.get("zip", ""),
        "country": shipping.get("country_code", "ZA"),
    }
    # Shipping address doesn't include email
    shipping_addr = {k: v for k, v in billing.items() if k != "email"}

    line_items = [
        {"product_id": item["product_id"], "quantity": item.get("quantity", 1)}
        for item in order_data.get("line_items", [])
    ]

    payload = {
        "payment_method": "cod",
        "payment_method_title": "Cash on Delivery",
        "set_paid": False,
        "billing": billing,
        "shipping": shipping_addr,
        "line_items": line_items,
        "customer_note": "Placed via AI shopping assistant. Payment: Cash on Delivery.",
    }

    with httpx.Client(timeout=15) as http:
        resp = http.post(
            f"{WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/orders",
            json=payload,
            params={
                "consumer_key": WC_CONSUMER_KEY,
                "consumer_secret": WC_CONSUMER_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()


def _send_defect_email(report: dict) -> bool:
    """Send a defective order notification to the store owner via SMTP.
    Returns True on success, False on any failure."""
    if not SMTP_USER or not SMTP_PASSWORD or not STORE_OWNER_EMAIL:
        print("[email] SMTP credentials or STORE_OWNER_EMAIL not set — skipping email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ Defective order report — {report.get('order_number', 'unknown')}"
    msg["From"]    = SMTP_USER
    msg["To"]      = STORE_OWNER_EMAIL

    html = (
        f"<h2>⚠️ Defective Order Report</h2>"
        f"<p><strong>Customer:</strong> {report.get('customer_name', 'Unknown')}</p>"
        f"<p><strong>Order number:</strong> {report.get('order_number', 'Not provided')}</p>"
        f"<p><strong>Issue:</strong> {report.get('issue', 'No description')}</p>"
        f"<p><strong>Contact:</strong> {report.get('contact', 'Not provided')}</p>"
    )
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, STORE_OWNER_EMAIL, msg.as_string())
        print(f"[email] Defect report sent for order {report.get('order_number')}")
        return True
    except Exception as exc:
        print(f"[email] Failed to send defect report: {exc}")
        return False


# ── WhatsApp (Twilio) — set WHATSAPP_ENABLED = True in this file to activate ──

def _send_whatsapp_reply(to: str, reply_text: str, image_urls: list[str] | None = None) -> None:
    """Send a WhatsApp message via Twilio REST API."""
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    media = (image_urls or [])[:10] or None
    twilio_client.messages.create(body=reply_text, from_=TWILIO_WHATSAPP_FROM, to=to, media_url=media)


def _run_agent_and_reply(session_id: str, user_message: str, phone_number: str) -> None:
    """Run in thread executor: acquire per-number lock, then get agent reply and push via Twilio."""
    lock = _get_whatsapp_lock(phone_number)
    if not lock.acquire(blocking=False):
        print(f"[whatsapp] dropping concurrent message from {phone_number}")
        return
    try:
        _run_agent_and_reply_inner(session_id, user_message, phone_number)
    finally:
        lock.release()


def _run_agent_and_reply_inner(session_id: str, user_message: str, phone_number: str) -> None:
    last_text = ""
    rate_limited = False
    try:
        with client.beta.sessions.events.stream(session_id) as stream:
            client.beta.sessions.events.send(
                session_id,
                events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": user_message}],
                    }
                ],
            )
            for event in stream:
                if event.type == "agent.custom_tool_use" and event.name == "search_products":
                    try:
                        results = _search_wc_products(
                            event.input.get("query", ""),
                            min(int(event.input.get("per_page", 10)), 25),
                        )
                        client.beta.sessions.events.send(
                            session_id,
                            events=[{
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": event.id,
                                "content": [{"type": "text", "text": json.dumps(results)}],
                            }],
                        )
                    except Exception as exc:
                        client.beta.sessions.events.send(
                            session_id,
                            events=[{
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": event.id,
                                "is_error": True,
                                "content": [{"type": "text", "text": str(exc)}],
                            }],
                        )
                elif event.type == "agent.message":
                    for block in event.content:
                        text = getattr(block, "text", None)
                        if text:
                            last_text = text  # overwrite — only the final reply matters
                elif event.type == "session.error":
                    err = getattr(event, "error", None)
                    if err and getattr(err, "type", None) == "model_rate_limited_error":
                        rate_limited = True
                elif event.type == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    if stop and getattr(stop, "type", None) == "retries_exhausted":
                        rate_limited = True
                    break
    except Exception as exc:
        print(f"[whatsapp] agent error: {exc}")
        _send_whatsapp_reply(phone_number, "Sorry, something went wrong. Please try again.")
        return

    if rate_limited or not last_text:
        _send_whatsapp_reply(
            phone_number,
            "I'm a bit busy right now — please send your message again in a moment.",
        )
        return

    raw_reply = last_text
    image_urls: list[str] = []

    # Order block — create WooCommerce order and build confirmation message
    order_match = re.search(r"```order\s*(\{.*?\})\s*```", raw_reply, re.DOTALL)
    if order_match:
        preamble = raw_reply[:order_match.start()].rstrip()
        try:
            order_data = json.loads(order_match.group(1))
            result = _create_wc_order(order_data)
            order_num = result.get("number") or result.get("id")
            reply_text = f"{preamble}\n\n✅ Order #{order_num} confirmed! We'll be in touch to arrange delivery. Payment is due on arrival — no card needed."
        except Exception as exc:
            print(f"[whatsapp] order creation error: {exc}")
            reply_text = f"{preamble}\n\nSorry, we couldn't place your order right now. Please try again."
    else:
        defect_match = re.search(r"```defective_report\s*(\{.*?\})\s*```", raw_reply, re.DOTALL)
        if defect_match:
            preamble = raw_reply[:defect_match.start()].rstrip()
            sent = False
            try:
                report = json.loads(defect_match.group(1))
                sent = _send_defect_email(report)
            except Exception as exc:
                print(f"[whatsapp] defect report error: {exc}")
            if sent:
                reply_text = f"{preamble}\n\n✅ Your report has been submitted. The store team will follow up with you within 24 hours."
            else:
                reply_text = f"{preamble}\n\n⚠️ Sorry, we couldn't submit your report right now. Please contact us directly and we'll sort this out immediately."
        else:
            # Products block — extract images then strip the JSON for WhatsApp
            prod_match = re.search(r"```products\s*(\[.*?\])\s*```", raw_reply, re.DOTALL)
            if prod_match:
                try:
                    products = json.loads(prod_match.group(1))
                    image_urls = [p["image_url"] for p in products if p.get("image_url")]
                except (json.JSONDecodeError, KeyError):
                    pass
                reply_text = raw_reply[:prod_match.start()].rstrip()
            else:
                reply_text = raw_reply

    # Truncate to WhatsApp's 1600-character limit
    if len(reply_text) > 1600:
        reply_text = reply_text[:1597] + "..."

    _send_whatsapp_reply(
        phone_number,
        reply_text or "Sorry, I didn't get a response. Please try again.",
        image_urls=image_urls or None,
    )


@app.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str | None = Form(default=None),
    Body: str | None = Form(default=None),
):
    """
    Twilio posts incoming WhatsApp messages here.
    Returns empty TwiML immediately (avoids Twilio's 15s timeout), then
    sends the agent reply asynchronously via the Twilio REST API.

    To activate: set WHATSAPP_ENABLED = True above and fill Twilio credentials in .env.
    """
    if not WHATSAPP_ENABLED:
        raise HTTPException(503, "WhatsApp channel is not enabled — set WHATSAPP_ENABLED = True in main.py")

    if not agent_id or not environment_id:
        raise HTTPException(503, "Agent not ready yet")

    form_data = dict(await request.form())

    if not From or Body is None:
        return Response(status_code=204)

    if TWILIO_AUTH_TOKEN:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if request.headers.get("X-Forwarded-Proto") == "https" and url.startswith("http://"):
            url = "https://" + url[7:]
        if not validator.validate(url, form_data, signature):
            pass  # TODO: enforce once Twilio credentials are active

    phone_number = From
    user_message = Body.strip()

    session_id = whatsapp_sessions.get(phone_number)
    if not session_id:
        session = client.beta.sessions.create(
            agent=agent_id,
            environment_id=environment_id,
            title=f"WhatsApp session {phone_number}",
        )
        session_id = session.id
        whatsapp_sessions[phone_number] = session_id

    background_tasks.add_task(
        lambda: executor.submit(_run_agent_and_reply, session_id, user_message, phone_number)
    )

    return Response(content=str(MessagingResponse()), media_type="application/xml")
