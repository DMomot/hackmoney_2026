import asyncio
import json
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from adapters import polymarket, limitless, opinion
from utils.utils import build_pooled, find_optimal_route

load_dotenv()

app = FastAPI()

app.mount("/public", StaticFiles(directory="public"), name="public")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/market")
async def market():
    return FileResponse("static/market.html")

ADAPTERS = {
    "polymarket": polymarket,
    "limitless": limitless,
    "opinion": opinion,
}

PLATFORM_FILES = {
    "polymarket": "static/polymarket_tokens.json",
    "limitless": "static/limitless_slugs.json",
    "opinion": "static/opinion_tokens.json",
}

def _load_platform_teams():
    """Load which teams each platform supports, keyed by event_id."""
    data = {}
    for platform, path in PLATFORM_FILES.items():
        with open(path) as f:
            raw = json.load(f)
        for event_id, info in raw.items():
            data.setdefault(event_id, {})
            for team in info.get("teams", {}):
                data[event_id].setdefault(team, []).append(platform)
    return data

_platform_teams = _load_platform_teams()


@app.get("/api/config")
async def config():
    return {"wc_project_id": os.getenv("WALLET_CONNECT_PROJECT_ID", "")}


@app.get("/api/event-platforms")
async def event_platforms(event_id: str = Query(...)):
    """Return mapping team -> list of platforms that have this outcome."""
    return _platform_teams.get(event_id, {})


def _build_side(books: list[dict], team: str, side: str) -> dict:
    """Build pooled orderbook for one side from a list of platform books."""
    asks = sorted(build_pooled(books, "asks"), key=lambda x: x["price"])
    bids = sorted(build_pooled(books, "bids"), key=lambda x: x["price"], reverse=True)

    return {
        "platform": "pooled",
        "team": team,
        "side": side,
        "asks": asks,
        "bids": bids,
        "best_ask": asks[0]["price_cents"] if asks else 0,
        "best_bid": bids[0]["price_cents"] if bids else 0,
    }


@app.get("/api/orderbook/all")
async def orderbook_all(
    event_id: str = Query(...),
    team: str = Query(...),
):
    """Fetch yes+no orderbooks from all platforms in parallel."""
    # Build tasks for both sides across all platforms
    tasks = {}
    for side in ("yes", "no"):
        for name, adapter in ADAPTERS.items():
            tasks[f"{name}_{side}"] = adapter.get_orderbook(event_id, team, side)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    task_keys = list(tasks.keys())

    # Sort results per side
    sides = {}
    for side in ("yes", "no"):
        platforms = {}
        books = []
        for key, res in zip(task_keys, results):
            name, s = key.rsplit("_", 1)
            if s != side:
                continue
            if isinstance(res, Exception):
                platforms[name] = {"error": str(res)}
            else:
                platforms[name] = res
                books.append(res)

        sides[side] = {
            "platforms": platforms,
            "pooled": _build_side(books, team, side),
        }

    return sides


@app.get("/api/route")
async def route(
    event_id: str = Query(...),
    team: str = Query(...),
    side: str = Query("yes"),
    budget: float = Query(...),
    direction: str = Query("buy"),
):
    """Find optimal order route across all platforms."""
    # Fetch full orderbooks in parallel
    tasks = {
        name: adapter.get_orderbook(event_id, team, side)
        for name, adapter in ADAPTERS.items()
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    full_books = []
    errors = {}
    for name, res in zip(tasks.keys(), results):
        if isinstance(res, Exception):
            errors[name] = str(res)
        else:
            full_books.append(res)

    result = find_optimal_route(full_books, budget, direction)
    if errors:
        result["adapter_errors"] = errors
    return result
