import httpx
import json
import os
from pathlib import Path

API_URL = "https://api.limitless.exchange"
SLUGS_PATH = Path(__file__).parent.parent / "static" / "limitless_slugs.json"
USDC_DECIMALS = 6

def _load_slugs():
    with open(SLUGS_PATH) as f:
        return json.load(f)

def _api_key():
    return os.getenv("LIMITLESS_API_KEY", "")

def _enrich(levels):
    cumsum = 0
    for lv in levels:
        lv["total"] = round(lv["price"] * lv["size"], 2)
        lv["price_cents"] = round(lv["price"] * 100, 1)
        cumsum += lv["total"]
        lv["cumsum"] = round(cumsum, 2)
    return levels


async def get_orderbook(event_id: str, team: str, side: str = "yes") -> dict:
    """Fetch full orderbook from Limitless."""
    slugs = _load_slugs()
    event = slugs.get(event_id)
    if not event:
        return {"error": f"Event {event_id} not found"}
    slug = event["teams"].get(team)
    if not slug:
        return {"error": f"Team {team} not found in {event_id}"}

    headers = {}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_URL}/markets/{slug}/orderbook", headers=headers)
        data = resp.json()

    divisor = 10 ** USDC_DECIMALS
    raw_bids = [{"price": float(b["price"]), "size": float(b["size"]) / divisor} for b in data.get("bids", [])]
    raw_asks = [{"price": float(a["price"]), "size": float(a["size"]) / divisor} for a in data.get("asks", [])]

    if side == "no":
        raw_bids, raw_asks = (
            [{"price": 1 - a["price"], "size": a["size"]} for a in raw_asks],
            [{"price": 1 - b["price"], "size": b["size"]} for b in raw_bids],
        )

    asks = _enrich(sorted(raw_asks, key=lambda x: x["price"]))
    bids = _enrich(sorted(raw_bids, key=lambda x: x["price"], reverse=True))

    return {
        "platform": "limitless",
        "team": team, "side": side,
        "asks": asks, "bids": bids,
        "best_ask": asks[0]["price_cents"] if asks else 0,
        "best_bid": bids[0]["price_cents"] if bids else 0,
    }
