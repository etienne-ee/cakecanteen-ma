import os
import json
import httpx
from fastmcp import FastMCP

WC_STORE_URL      = os.environ["WC_STORE_URL"]
WC_CONSUMER_KEY   = os.environ["WC_CONSUMER_KEY"]
WC_CONSUMER_SECRET = os.environ["WC_CONSUMER_SECRET"]

mcp = FastMCP("WooCommerce MCP")


@mcp.tool()
def search_products(query: str, per_page: int = 10) -> str:
    """Search for published products in the CakeCart WooCommerce store by keyword.
    Returns product IDs, names, prices, images, stock status, and permalinks."""
    per_page = min(per_page, 25)
    with httpx.Client(timeout=10) as http:
        resp = http.get(
            f"{WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products",
            params={
                "search": query,
                "per_page": per_page,
                "consumer_key": WC_CONSUMER_KEY,
                "consumer_secret": WC_CONSUMER_SECRET,
            },
            headers={"User-Agent": "Mozilla/5.0 (compatible; CakeCartBot/1.0)"},
        )
        resp.raise_for_status()
        products = resp.json()
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
            for p in products
        ])


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
    )
