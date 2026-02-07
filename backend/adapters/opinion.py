import httpx
import json
import os
from pathlib import Path

API_URL = "https://openapi.opinion.trade/openapi"
TOKENS_PATH = Path(__file__).parent.parent / "static" / "opinion_tokens.json"

def _load_tokens():
    with open(TOKENS_PATH) as f:
        return json.load(f)

def _api_key():
    return os.getenv("OPINION_API_KEY", "")

def _enrich(levels):
    cumsum = 0
    for lv in levels:
        lv["total"] = round(lv["price"] * lv["size"], 2)
        lv["price_cents"] = round(lv["price"] * 100, 1)
        cumsum += lv["total"]
        lv["cumsum"] = round(cumsum, 2)
    return levels


async def get_orderbook(event_id: str, team: str, side: str = "yes") -> dict:
    """Fetch full orderbook from Opinion CLOB."""
    tokens = _load_tokens()
    event = tokens.get(event_id)
    if not event:
        return {"error": f"Event {event_id} not found"}
    team_data = event["teams"].get(team)
    if not team_data:
        return {"error": f"Team {team} not found in {event_id}"}
    market_id = team_data.get("market_id")
    token_id = team_data.get(side)
    if not token_id:
        return {"error": f"Side {side} not found for {team}"}

    headers = {"apikey": _api_key()}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_URL}/token/orderbook", params={"token_id": token_id}, headers=headers)
        data = resp.json()

    result = data.get("result", {})
    raw_bids = [{"price": float(b["price"]), "size": float(b["size"])} for b in result.get("bids", [])]
    raw_asks = [{"price": float(a["price"]), "size": float(a["size"])} for a in result.get("asks", [])]

    asks = _enrich(sorted(raw_asks, key=lambda x: x["price"]))
    bids = _enrich(sorted(raw_bids, key=lambda x: x["price"], reverse=True))

    return {
        "platform": "opinion",
        "market_id": market_id,
        "token_id": token_id,
        "team": team, "side": side,
        "asks": asks, "bids": bids,
        "best_ask": asks[0]["price_cents"] if asks else 0,
        "best_bid": bids[0]["price_cents"] if bids else 0,
    }
