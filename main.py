import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from web3 import Web3
from eth_account import Account
from adapters import polymarket, limitless, opinion
from utils.utils import build_pooled, find_optimal_route
import httpx

load_dotenv()

app = FastAPI()

# --- Web3 setup for relay ---
BASE_RPC = "https://mainnet.base.org"
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
OWNER_KEY = os.getenv("OWNER_PRIVATE_KEY", "")
OWNER_ACCOUNT = Account.from_key(OWNER_KEY) if OWNER_KEY else None
ROUTER_ADDRESS = os.getenv("ROUTER_ADDRESS", "")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
LIFI_DIAMOND = "0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE"

USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon

ROUTER_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"address","name":"from","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"address","name":"lifiDiamond","type":"address"},{"internalType":"bytes","name":"lifiData","type":"bytes"},{"internalType":"bytes","name":"metadata","type":"bytes"}],"name":"bridgeViaLiFi","outputs":[],"stateMutability":"nonpayable","type":"function"}]')

ORDERS_FILE = "static/orders.json"

def _load_orders():
    if not os.path.exists(ORDERS_FILE):
        return []
    with open(ORDERS_FILE) as f:
        return json.load(f)

def _save_orders(orders):
    with open(ORDERS_FILE, "w") as f:
        json.dump(orders, f, indent=2)

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
    return {
        "wc_project_id": os.getenv("WALLET_CONNECT_PROJECT_ID", ""),
        "router_address": ROUTER_ADDRESS,
        "usdc_address": USDC_BASE,
    }


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


# ---- Order API ----

@app.post("/api/order")
async def create_order(body: dict = Body(...)):
    """Create order, relay transferERC20 on-chain, return tx_hash."""
    order_id = str(uuid.uuid4())[:8]
    order = {
        "id": order_id,
        "wallet": body["wallet"],
        "event_id": body["event_id"],
        "team": body["team"],
        "side": body["side"],
        "budget": body["budget"],
        "route": body.get("route", {}),
        "status": "pending",
        "approve_tx_hash": body.get("approve_tx_hash"),
        "tx_hash": None,
        "receiving_tx_hash": None,
        "receiving_chain_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Relay: get LiFi quote, then call router.bridgeViaLiFi
    try:
        router_addr = Web3.to_checksum_address(ROUTER_ADDRESS)
        user_addr = Web3.to_checksum_address(body["wallet"])
        amount_raw = int(body["budget"] * 1e6)  # USDC 6 decimals

        # Get LiFi quote: Base USDC → Polygon USDC.e
        import requests
        lifi_resp = requests.get("https://li.quest/v1/quote", params={
            "fromChain": 8453,
            "toChain": 137,
            "fromToken": USDC_BASE,
            "toToken": USDC_POLYGON,
            "fromAmount": str(amount_raw),
            "fromAddress": router_addr,
            "toAddress": user_addr,
            "slippage": "0.05",
            "integrator": "premarket-router",
        }, timeout=15)
        lifi_quote = lifi_resp.json()
        lifi_data = lifi_quote["transactionRequest"]["data"]

        metadata = Web3.to_bytes(text=json.dumps({
            "order_id": order_id,
            "event_id": body["event_id"],
            "team": body["team"],
            "side": body["side"],
        }))

        router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
        tx = router.functions.bridgeViaLiFi(
            Web3.to_checksum_address(USDC_BASE),
            user_addr,
            amount_raw,
            Web3.to_checksum_address(LIFI_DIAMOND),
            bytes.fromhex(lifi_data[2:]),  # strip 0x
            metadata,
        ).build_transaction({
            "from": OWNER_ACCOUNT.address,
            "nonce": w3.eth.get_transaction_count(OWNER_ACCOUNT.address, "pending"),
            "gas": 500000,
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.eth.max_priority_fee,
            "chainId": 8453,
        })

        signed = OWNER_ACCOUNT.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        order["tx_hash"] = "0x" + tx_hash.hex()
        order["status"] = "sent"
    except Exception as e:
        order["status"] = "failed"
        order["error"] = str(e)

    orders = _load_orders()
    orders.append(order)
    _save_orders(orders)

    return order


@app.get("/api/order/{order_id}")
async def get_order(order_id: str):
    """Get order status."""
    orders = _load_orders()
    for o in orders:
        if o["id"] == order_id:
            return o
    return {"error": "not found"}


# ---- LiFi Status Poller ----

async def poll_orders():
    """Background task: poll LiFi status for sent orders."""
    while True:
        await asyncio.sleep(10)
        orders = _load_orders()
        changed = False
        for o in orders:
            if o["status"] != "sent" or not o.get("tx_hash"):
                continue
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://li.quest/v1/status",
                        params={"txHash": o["tx_hash"]},
                        timeout=10,
                    )
                    data = resp.json()
                    lifi_status = data.get("status", "")
                    if lifi_status == "DONE":
                        o["status"] = "filled"
                        recv = data.get("receiving", {})
                        o["receiving_tx_hash"] = recv.get("txHash")
                        o["receiving_chain_id"] = recv.get("chainId")
                        changed = True
                    elif lifi_status == "FAILED":
                        o["status"] = "failed"
                        changed = True
            except Exception:
                pass
        if changed:
            _save_orders(orders)


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_orders())
