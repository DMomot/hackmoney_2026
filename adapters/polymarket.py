import httpx
import json
from pathlib import Path

CLOB_URL = "https://clob.polymarket.com"
TOKENS_PATH = Path(__file__).parent.parent / "static" / "polymarket_tokens.json"

def _load_tokens():
    with open(TOKENS_PATH) as f:
        return json.load(f)

async def get_orderbook(event_id: str, team: str, side: str = "yes") -> dict:
    """Fetch orderbook from Polymarket CLOB for a specific team/outcome."""
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

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{CLOB_URL}/book", params={"token_id": token_id})
        data = resp.json()

    bids = [{"price": b["price"], "size": b["size"]} for b in data.get("bids", [])]
    asks = [{"price": a["price"], "size": a["size"]} for a in data.get("asks", [])]

    return {
        "platform": "polymarket",
        "event": event["event_title"],
        "team": team,
        "side": side,
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
    }

async def get_teams(event_id: str) -> list[str]:
    """Return list of available teams for an event."""
    tokens = _load_tokens()
    event = tokens.get(event_id)
    if not event:
        return []
    return list(event["teams"].keys())
