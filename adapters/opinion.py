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

async def get_orderbook(event_id: str, team: str, side: str = "yes") -> dict:
    """Fetch orderbook from Opinion CLOB for a specific team/outcome."""
    tokens = _load_tokens()
    event = tokens.get(event_id)
    if not event:
        return {"error": f"Event {event_id} not found"}

    team_data = event["teams"].get(team)
    if not team_data:
        return {"error": f"Team {team} not found in {event_id}"}

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
        "platform": "opinion",
        "event": event["event_title"],
        "team": team,
        "side": side,
        "token_id": token_id,
        "asks": asks,
        "bids": bids,
        "best_ask": round(best_ask * 100, 1),
        "best_bid": round(best_bid * 100, 1),
    }

async def get_teams(event_id: str) -> list[str]:
    """Return list of available teams for an event."""
    tokens = _load_tokens()
    event = tokens.get(event_id)
    if not event:
        return []
    return list(event["teams"].keys())
