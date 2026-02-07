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

async def get_orderbook(event_id: str, team: str, side: str = "yes") -> dict:
    """Fetch orderbook from Limitless for a specific team."""
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

    # Size is in USDC raw (6 decimals)
    divisor = 10 ** USDC_DECIMALS

    raw_bids = [{"price": float(b["price"]), "size": float(b["size"]) / divisor} for b in data.get("bids", [])]
    raw_asks = [{"price": float(a["price"]), "size": float(a["size"]) / divisor} for a in data.get("asks", [])]

    # Limitless orderbook is for Yes token; if side=no, invert prices
    if side == "no":
        raw_bids, raw_asks = (
            [{"price": 1 - a["price"], "size": a["size"]} for a in raw_asks],
            [{"price": 1 - b["price"], "size": b["size"]} for b in raw_bids],
        )

    # 5 lowest asks, reversed for display
    asks = sorted(raw_asks, key=lambda x: x["price"])[:5][::-1]
    # 5 highest bids
    bids = sorted(raw_bids, key=lambda x: x["price"], reverse=True)[:5]

    for level in asks + bids:
        level["total"] = round(level["price"] * level["size"], 2)
        level["price_cents"] = round(level["price"] * 100, 1)

    best_ask = asks[-1]["price"] if asks else 0
    best_bid = bids[0]["price"] if bids else 0

    return {
        "platform": "limitless",
        "event": event["event_title"],
        "team": team,
        "side": side,
        "slug": slug,
        "asks": asks,
        "bids": bids,
        "best_ask": round(best_ask * 100, 1),
        "best_bid": round(best_bid * 100, 1),
    }

async def get_teams(event_id: str) -> list[str]:
    """Return list of available teams for an event."""
    slugs = _load_slugs()
    event = slugs.get(event_id)
    if not event:
        return []
    return list(event["teams"].keys())
