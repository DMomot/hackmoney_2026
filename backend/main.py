import asyncio
import json
import logging
import math
import os
import sys
import time
import threading
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
import requests as req_lib

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
logger = logging.getLogger(__name__)


app = FastAPI()

# --- Web3 setup for relay ---
BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
OWNER_KEY = os.getenv("OWNER_PRIVATE_KEY", "")
OWNER_ACCOUNT = Account.from_key(OWNER_KEY) if OWNER_KEY else None
ROUTER_ADDRESS = os.getenv("ROUTER_ADDRESS", "")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
LIFI_DIAMOND = "0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE"

USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"  # USDT on BSC

# Router per chain
CHAIN_ROUTER = {
    8453: ROUTER_ADDRESS,
    56: "0xB80998ac4e349Bb34dA07823eF950ec32108F2E8",
    137: "0x5a5626DC1e713438EB62f7b6Fb81add038f74BcC",
}

# Platform chain mapping
PLATFORM_CHAIN = {"polymarket": 137, "opinion": 56, "limitless": 8453}
PLATFORM_STABLE = {"polymarket": USDC_POLYGON, "opinion": USDT_BSC, "limitless": USDC_BASE}
PLATFORM_DECIMALS = {"polymarket": 6, "opinion": 18, "limitless": 6}

# Chain → stablecoin mapping
CHAIN_STABLE = {8453: USDC_BASE, 137: USDC_POLYGON, 56: USDT_BSC}
CHAIN_DECIMALS = {8453: 6, 137: 6, 56: 18}

# --- Polymarket trading adapter (relayer uses OWNER key) ---
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
BSC_RPC = os.getenv("BSC_RPC", "https://bsc-rpc.publicnode.com")
RELAYER_KEY = OWNER_KEY  # relayer = owner of router
_poly_adapter = None

def _get_poly_adapter():
    global _poly_adapter
    if _poly_adapter is None and RELAYER_KEY:
        import importlib.util, types
        relayer_dir = os.path.join(os.path.dirname(__file__), '..', 'relayer', 'adapters')
        # Load base module
        spec_b = importlib.util.spec_from_file_location("relayer_adapters.base", os.path.join(relayer_dir, "base.py"))
        base_mod = importlib.util.module_from_spec(spec_b)
        spec_b.loader.exec_module(base_mod)
        # Register as package so relative import works
        pkg = types.ModuleType("relayer_adapters")
        pkg.__path__ = [relayer_dir]
        sys.modules["relayer_adapters"] = pkg
        sys.modules["relayer_adapters.base"] = base_mod
        # Load polymarket module
        spec_p = importlib.util.spec_from_file_location("relayer_adapters.polymarket", os.path.join(relayer_dir, "polymarket.py"),
                                                         submodule_search_locations=[])
        poly_mod = importlib.util.module_from_spec(spec_p)
        poly_mod.__package__ = "relayer_adapters"
        sys.modules["relayer_adapters.polymarket"] = poly_mod
        spec_p.loader.exec_module(poly_mod)
        PolyTrade = poly_mod.PolymarketAdapter
        relayer_addr = Account.from_key(RELAYER_KEY).address
        _poly_adapter = PolyTrade(
            private_key=RELAYER_KEY,
            proxy_wallet=relayer_addr,
            rpc_url=POLYGON_RPC,
        )
        _poly_adapter.authenticate()
        logger.info(f"Polymarket adapter ready, relayer: {relayer_addr}")
    return _poly_adapter

# --- Opinion trading adapter ---
_opinion_adapter = None

def _get_opinion_adapter():
    global _opinion_adapter
    if _opinion_adapter is not None:
        return _opinion_adapter
    opinion_key = os.getenv("OPINION_PRIVATE_KEY", "")
    opinion_wallet = os.getenv("OPINION_WALLET_ADDRESS", "")
    if not opinion_key or not opinion_wallet:
        return None
    import importlib.util, types
    relayer_dir = os.path.join(os.path.dirname(__file__), '..', 'relayer', 'adapters')
    # Ensure package registered
    if "relayer_adapters" not in sys.modules:
        spec_b = importlib.util.spec_from_file_location("relayer_adapters.base", os.path.join(relayer_dir, "base.py"))
        base_mod = importlib.util.module_from_spec(spec_b)
        spec_b.loader.exec_module(base_mod)
        pkg = types.ModuleType("relayer_adapters")
        pkg.__path__ = [relayer_dir]
        sys.modules["relayer_adapters"] = pkg
        sys.modules["relayer_adapters.base"] = base_mod
    # Load opinion module
    spec_o = importlib.util.spec_from_file_location("relayer_adapters.opinion", os.path.join(relayer_dir, "opinion.py"),
                                                     submodule_search_locations=[])
    op_mod = importlib.util.module_from_spec(spec_o)
    op_mod.__package__ = "relayer_adapters"
    sys.modules["relayer_adapters.opinion"] = op_mod
    spec_o.loader.exec_module(op_mod)
    _opinion_adapter = op_mod.OpinionAdapter(
        private_key=opinion_key,
        smart_wallet=opinion_wallet,
        main_relayer_key=RELAYER_KEY,
    )
    _opinion_adapter.authenticate()
    logger.info(f"Opinion adapter ready, wallet: {opinion_wallet}")
    return _opinion_adapter

# --- Limitless trading adapter ---
_limitless_adapter = None

def _get_limitless_adapter():
    global _limitless_adapter
    if _limitless_adapter is not None:
        return _limitless_adapter
    if not RELAYER_KEY:
        return None
    import importlib.util, types
    relayer_dir = os.path.join(os.path.dirname(__file__), '..', 'relayer', 'adapters')
    if "relayer_adapters" not in sys.modules:
        spec_b = importlib.util.spec_from_file_location("relayer_adapters.base", os.path.join(relayer_dir, "base.py"))
        base_mod = importlib.util.module_from_spec(spec_b)
        spec_b.loader.exec_module(base_mod)
        pkg = types.ModuleType("relayer_adapters")
        pkg.__path__ = [relayer_dir]
        sys.modules["relayer_adapters"] = pkg
        sys.modules["relayer_adapters.base"] = base_mod
    spec_l = importlib.util.spec_from_file_location("relayer_adapters.limitless", os.path.join(relayer_dir, "limitless.py"),
                                                     submodule_search_locations=[])
    lim_mod = importlib.util.module_from_spec(spec_l)
    lim_mod.__package__ = "relayer_adapters"
    sys.modules["relayer_adapters.limitless"] = lim_mod
    spec_l.loader.exec_module(lim_mod)
    _limitless_adapter = lim_mod.LimitlessAdapter(
        private_key=RELAYER_KEY,
        rpc_url=BASE_RPC,
    )
    _limitless_adapter.authenticate()
    relayer_addr = Account.from_key(RELAYER_KEY).address
    logger.info(f"Limitless adapter ready, relayer: {relayer_addr}")
    return _limitless_adapter

def _get_adapter(platform: str):
    """Get adapter by platform name."""
    if platform == "polymarket":
        return _get_poly_adapter()
    elif platform == "opinion":
        return _get_opinion_adapter()
    elif platform == "limitless":
        return _get_limitless_adapter()
    return None

ROUTER_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"address","name":"from","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"address","name":"lifiDiamond","type":"address"},{"internalType":"bytes","name":"lifiData","type":"bytes"},{"internalType":"bytes","name":"metadata","type":"bytes"}],"name":"bridgeViaLiFi","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"address","name":"from","type":"address"},{"internalType":"uint8","name":"platformId","type":"uint8"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"bytes","name":"metadata","type":"bytes"}],"name":"transferERC20","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"address","name":"from","type":"address"},{"internalType":"uint8","name":"platformId","type":"uint8"},{"internalType":"uint256","name":"tokenId","type":"uint256"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"bytes","name":"metadata","type":"bytes"}],"name":"transferERC1155","outputs":[],"stateMutability":"nonpayable","type":"function"}]')

# Platform IDs in Router contract
PLATFORM_ROUTER_ID = {"polymarket": 1, "opinion": 2, "limitless": 3}

ORDERS_FILE = "static/orders.json"
_orders_lock = threading.Lock()

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
logger.info(f"Platform teams loaded: {list(_platform_teams.keys())}")


@app.get("/api/config")
async def config():
    relayer_addr = Account.from_key(RELAYER_KEY).address if RELAYER_KEY else ""
    return {
        "wc_project_id": os.getenv("WALLET_CONNECT_PROJECT_ID", ""),
        "router_address": ROUTER_ADDRESS,
        "chain_routers": CHAIN_ROUTER,
        "usdc_address": USDC_BASE,
        "relayer_address": relayer_addr,
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
        elif isinstance(res, dict) and "error" in res:
            errors[name] = res["error"]
        else:
            full_books.append(res)

    result = find_optimal_route(full_books, budget, direction)
    if errors:
        result["adapter_errors"] = errors
    return result


@app.post("/api/test_batch_route")
async def test_batch_route(body: dict = Body(...)):
    """Split budget 50/50 between Polymarket and Limitless. Buy what's available."""
    budget = body.get("budget", 6)
    event_id = body.get("event_id", "anthropic-acquired-2027")
    team = body.get("team", "Anthropic acquired before 2027?")
    side = body.get("side", "yes")
    half = budget / 2

    per_platform = {}
    for pname in ["polymarket", "limitless"]:
        adapter = ADAPTERS.get(pname)
        if not adapter:
            continue
        try:
            book = await adapter.get_orderbook(event_id, team, side)
            r = find_optimal_route([book], half, "buy")
            if "error" not in r and r.get("per_platform"):
                per_platform[pname] = r["per_platform"][pname]
        except Exception as e:
            per_platform[pname] = {"error": str(e)}

    total_spent = sum(p.get("spent", 0) for p in per_platform.values() if "error" not in p)
    total_qty = sum(p.get("qty", 0) for p in per_platform.values() if "error" not in p)
    return {
        "direction": "buy",
        "budget": budget,
        "total_spent": round(total_spent, 4),
        "total_qty": round(total_qty, 4),
        "avg_price": round(total_spent / total_qty, 6) if total_qty > 0 else 0,
        "avg_price_cents": round(total_spent / total_qty * 100, 2) if total_qty > 0 else 0,
        "unfilled": round(budget - total_spent, 4),
        "platforms_used": len([p for p in per_platform.values() if "error" not in p]),
        "per_platform": per_platform,
        "fills": [],
    }


# ---- Order API ----

@app.post("/api/order")
async def create_order(body: dict = Body(...)):
    """Create order, relay transferERC20 on-chain, return tx_hash."""
    order_id = str(uuid.uuid4())[:8]
    route = body.get("route", {})
    # Extract per-platform token_id / market_id from route
    platforms = {}
    for pname, pdata in route.get("per_platform", {}).items():
        entry = {}
        if "market_id" in pdata:
            entry["market_id"] = str(pdata["market_id"])
        if "token_id" in pdata:
            entry["token_id"] = str(pdata["token_id"])
        entry["spent"] = pdata.get("spent", 0)
        entry["qty"] = pdata.get("qty", 0)
        platforms[pname] = entry

    order = {
        "id": order_id,
        "wallet": body["wallet"],
        "event_id": body["event_id"],
        "team": body["team"],
        "side": body["side"],
        "budget": body["budget"],
        "route": route,
        "platforms": platforms,
        "status": "pending",
        "approve_tx_hash": body.get("approve_tx_hash"),
        "from_chain": body.get("from_chain", 8453),
        "tx_hash": None,
        "receiving_tx_hash": None,
        "receiving_chain_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Relay: pull stablecoin from user, bridge if needed
    try:
        router_addr = Web3.to_checksum_address(ROUTER_ADDRESS)
        user_addr = Web3.to_checksum_address(body["wallet"])

        # Determine source chain (from user selection, default Base)
        from_chain = body.get("from_chain", 8453)
        from_token = CHAIN_STABLE.get(from_chain, USDC_BASE)
        from_decimals = CHAIN_DECIMALS.get(from_chain, 6)
        amount_raw = int(body["budget"] * (10 ** from_decimals))

        # Group platforms by target chain
        chain_budgets = {}
        for pname, pdata in platforms.items():
            target = PLATFORM_CHAIN.get(pname, 137)
            chain_budgets[target] = chain_budgets.get(target, 0) + pdata.get("spent", 0)

        needs_bridge = not set(chain_budgets.keys()).issubset({from_chain})
        relayer_addr = Account.from_key(RELAYER_KEY).address if RELAYER_KEY else OWNER_ACCOUNT.address

        # to_address per chain for bridges (opinion has its own wallet)
        chain_to_addr = {}
        for pname in platforms:
            ch = PLATFORM_CHAIN.get(pname, 137)
            if pname == "opinion":
                chain_to_addr[ch] = os.getenv("OPINION_WALLET_ADDRESS", "")
            else:
                chain_to_addr.setdefault(ch, relayer_addr)

        # Connect to source chain
        from web3 import Web3 as W3
        chain_rpcs = {8453: BASE_RPC, 137: POLYGON_RPC, 56: BSC_RPC}
        src_w3 = W3(W3.HTTPProvider(chain_rpcs.get(from_chain, BASE_RPC)))

        # Step 1: Pull ALL funds from user via Router.transferERC20
        src_router_addr = W3.to_checksum_address(CHAIN_ROUTER.get(from_chain, ROUTER_ADDRESS))
        src_router = src_w3.eth.contract(address=src_router_addr, abi=ROUTER_ABI)
        pid = PLATFORM_ROUTER_ID.get(next(iter(platforms)), 0)
        metadata = W3.to_bytes(text=json.dumps({
            "order_id": order_id, "event_id": body["event_id"],
            "team": body["team"], "side": body["side"],
        }))
        nonce = src_w3.eth.get_transaction_count(OWNER_ACCOUNT.address, "pending")
        gas_price = src_w3.eth.gas_price
        pull_gas = {"maxFeePerGas": gas_price * 2, "maxPriorityFeePerGas": src_w3.eth.max_priority_fee} if from_chain == 8453 else {"gasPrice": int(gas_price * 1.5)}
        pull_tx = src_router.functions.transferERC20(
            W3.to_checksum_address(from_token), user_addr, pid, amount_raw, metadata,
        ).build_transaction({
            "from": OWNER_ACCOUNT.address, "nonce": nonce, "gas": 200000, "chainId": from_chain, **pull_gas,
        })
        signed = OWNER_ACCOUNT.sign_transaction(pull_tx)
        pull_hash = src_w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = src_w3.eth.wait_for_transaction_receipt(pull_hash, timeout=60)
        if receipt.status != 1:
            raise Exception(f"Router.transferERC20 reverted: 0x{pull_hash.hex()}")
        nonce += 1
        order["tx_hash"] = "0x" + pull_hash.hex()
        logger.info(f"Order {order_id}: pulled {amount_raw/(10**from_decimals):.2f} from user on chain {from_chain}")

        # Step 2: Bridge to target chains (or skip if all same chain)
        if not needs_bridge:
            order["status"] = "bridged"
        else:
            bridges = {}
            for target_chain, budget_usd in chain_budgets.items():
                if target_chain == from_chain:
                    # No bridge needed — funds already on this chain
                    bridges[str(target_chain)] = {"amount": int(budget_usd * (10 ** from_decimals)), "status": "done"}
                    continue

                bridge_amount = int(budget_usd * (10 ** from_decimals))
                to_token = CHAIN_STABLE.get(target_chain, USDC_BASE)
                to_addr = W3.to_checksum_address(chain_to_addr.get(target_chain, relayer_addr))

                # LiFi quote
                lifi_resp = req_lib.get("https://li.quest/v1/quote", params={
                    "fromChain": from_chain, "toChain": target_chain,
                    "fromToken": from_token, "toToken": to_token,
                    "fromAmount": str(bridge_amount), "fromAddress": relayer_addr,
                    "toAddress": to_addr, "slippage": "0.50", "integrator": "premarket-router",
                }, timeout=15)
                lifi_quote = lifi_resp.json()
                if "transactionRequest" not in lifi_quote:
                    raise Exception(f"LiFi quote error for chain {target_chain}: {json.dumps(lifi_quote)[:500]}")
                lifi_tx_req = lifi_quote["transactionRequest"]
                lifi_diamond = W3.to_checksum_address(lifi_tx_req["to"])

                # Approve LiFi Diamond
                approve_abi = [{"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                                "name": "approve", "outputs": [{"type": "bool"}], "type": "function"}]
                token_c = src_w3.eth.contract(address=W3.to_checksum_address(from_token), abi=approve_abi)
                appr_tx = token_c.functions.approve(lifi_diamond, bridge_amount).build_transaction({
                    "from": relayer_addr, "nonce": nonce, "gas": 80000,
                    "gasPrice": int(gas_price * 1.3), "chainId": from_chain,
                })
                signed_a = Account.from_key(RELAYER_KEY).sign_transaction(appr_tx)
                src_w3.eth.send_raw_transaction(signed_a.raw_transaction)
                src_w3.eth.wait_for_transaction_receipt(signed_a.hash, timeout=60)
                nonce += 1

                # Bridge tx — use gasLimit from LiFi
                lifi_gas = int(lifi_tx_req.get("gasLimit", "0"), 16) if isinstance(lifi_tx_req.get("gasLimit"), str) else int(lifi_tx_req.get("gasLimit", 0))
                if lifi_gas < 500000:
                    lifi_gas = 800000
                br_tx = {
                    "from": relayer_addr, "to": lifi_diamond, "data": lifi_tx_req["data"],
                    "value": int(lifi_tx_req.get("value", "0"), 16) if isinstance(lifi_tx_req.get("value"), str) else int(lifi_tx_req.get("value", 0)),
                    "nonce": nonce, "gas": lifi_gas, "gasPrice": int(gas_price * 1.5), "chainId": from_chain,
                }
                signed_b = Account.from_key(RELAYER_KEY).sign_transaction(br_tx)
                bh = src_w3.eth.send_raw_transaction(signed_b.raw_transaction)
                nonce += 1
                bh_hex = "0x" + bh.hex()
                bridges[str(target_chain)] = {"amount": bridge_amount, "bridge_tx": bh_hex, "status": "sent"}
                logger.info(f"Order {order_id}: bridge {budget_usd:.2f} to chain {target_chain}, tx={bh_hex}")

            order["bridges"] = bridges
            # Backward compat: set bridge_tx to first actual bridge
            first_br = next((b["bridge_tx"] for b in bridges.values() if b.get("bridge_tx")), None)
            if first_br:
                order["bridge_tx"] = first_br
            order["status"] = "sent"
    except Exception as e:
        order["status"] = "failed"
        order["error"] = str(e)

    with _orders_lock:
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


@app.get("/api/positions")
async def get_positions(wallet: str = Query(...), event_id: str = Query(None), team: str = Query(None), side: str = Query(None)):
    """Return on-chain balances per platform for filled buy orders."""
    orders = _load_orders()
    wallet_lower = wallet.lower()

    # Collect unique (platform, token_id) from filled buy orders
    token_map = {}  # (platform, token_id) -> {market_id, buy_price, event_id, team, side, order_id}
    for o in orders:
        if o.get("wallet", "").lower() != wallet_lower:
            continue
        if o.get("status") != "filled":
            continue
        if o.get("direction") == "sell":
            continue
        if event_id and o.get("event_id") != event_id:
            continue
        if team and o.get("team") != team:
            continue
        if side and o.get("side") != side:
            continue
        for pname, pdata in o.get("platforms", {}).items():
            tid = pdata.get("token_id")
            if not tid:
                continue
            key = (pname, tid)
            if key not in token_map:
                token_map[key] = {
                    "order_id": o["id"],
                    "market_id": pdata.get("market_id"),
                    "event_id": o.get("event_id"),
                    "team": o.get("team"),
                    "side": o.get("side"),
                    "buy_price": o.get("trade_results", {}).get(pname, {}).get("price", 0),
                    "budget": o.get("budget", 0),
                }

    # Query on-chain balance for each token
    positions = []
    for (platform, token_id), meta in token_map.items():
        try:
            adapter = _get_adapter(platform)
            if not adapter:
                continue
            # Opinion keeps shares on smart wallet, not user wallet
            if platform == "opinion":
                bal = adapter.get_shares_balance(token_id)
            else:
                bal = adapter.get_user_shares_balance(token_id, wallet)
            decimals = PLATFORM_DECIMALS.get(platform, 6)
            if bal / (10 ** decimals) < 1:
                continue
            positions.append({
                "order_id": meta["order_id"],
                "event_id": meta["event_id"],
                "team": meta["team"],
                "side": meta["side"],
                "platform": platform,
                "token_id": token_id,
                "market_id": meta["market_id"],
                "shares": round(bal / (10 ** decimals), 4),
                "shares_raw": bal,
                "buy_price": meta["buy_price"],
                "budget": meta["budget"],
            })
        except Exception as e:
            logger.error(f"Position balance check failed for {platform}/{token_id}: {e}")
    return positions


# ---- Sell API ----

@app.post("/api/sell")
async def create_sell(body: dict = Body(...)):
    """Sell shares: pull from user -> sell on Polymarket -> bridge USDC.e back to Base."""
    buy_order_id = body.get("order_id")
    sell_amount = body.get("amount")  # raw shares amount, optional (default = full)

    orders = _load_orders()
    buy_order = next((o for o in orders if o["id"] == buy_order_id), None)
    if not buy_order:
        return {"error": f"buy order {buy_order_id} not found"}
    if buy_order["status"] != "filled":
        return {"error": f"buy order not filled, status={buy_order['status']}"}

    user_wallet = buy_order["wallet"]

    # Detect platform from buy order
    platform = None
    token_id = None
    market_id = None
    for pname, pdata in buy_order.get("platforms", {}).items():
        if pdata.get("token_id"):
            platform = pname
            token_id = pdata["token_id"]
            market_id = pdata.get("market_id")
            break
    if not platform or not token_id:
        return {"error": "no token_id found in buy order"}

    adapter = _get_adapter(platform)
    if not adapter:
        return {"error": f"{platform} adapter not configured"}

    chain_id = PLATFORM_CHAIN.get(platform, 137)
    ctf_address = adapter.CONDITIONAL_TOKENS if hasattr(adapter, 'CONDITIONAL_TOKENS') else adapter.CTF_ADDRESS
    decimals = PLATFORM_DECIMALS.get(platform, 6)
    sell_id = str(uuid.uuid4())[:8]

    # Opinion: shares stay on smart wallet (API tracks internally), no pull needed
    if platform == "opinion":
        sw_shares = adapter.get_shares_balance(token_id)
        if sw_shares <= 0:
            return {"error": f"no shares on smart wallet for token {token_id}"}
        if sell_amount:
            shares_to_sell = int(float(sell_amount) * (10 ** decimals))
        else:
            shares_to_sell = sw_shares
        shares_to_sell = min(shares_to_sell, sw_shares)
        pull_tx = "skipped_opinion"
        logger.info(f"Sell {sell_id}: Opinion shares already on smart_wallet, balance={sw_shares}, to_sell={shares_to_sell}")
    else:
        # Check user has shares
        user_shares = adapter.get_user_shares_balance(token_id, user_wallet)
        if user_shares <= 0:
            return {"error": f"user has no shares for token {token_id}"}

        if sell_amount:
            shares_to_sell = int(float(sell_amount) * (10 ** decimals))
        else:
            shares_to_sell = user_shares
        shares_to_sell = min(shares_to_sell, user_shares)
        logger.info(f"Sell: user_shares={user_shares}, requested={sell_amount}, to_sell={shares_to_sell}")

        # Check operator approved by user on CTF — always Router
        chain_router_addr = CHAIN_ROUTER.get(chain_id, ROUTER_ADDRESS)
        operator = Web3.to_checksum_address(chain_router_addr)

        approved = adapter.check_erc1155_approval(user_wallet, operator)
        if not approved:
            return {
                "error": "operator not approved",
                "action": "setApprovalForAll",
                "ctf_address": ctf_address,
                "operator": operator,
                "chain_id": chain_id,
            }

        # Pull shares from user via Router.transferERC1155
        try:
            from web3 import Web3 as W3
            chain_rpcs = {8453: BASE_RPC, 137: POLYGON_RPC, 56: BSC_RPC}
            chain_w3 = W3(W3.HTTPProvider(chain_rpcs.get(chain_id, BASE_RPC)))
            router = chain_w3.eth.contract(address=operator, abi=ROUTER_ABI)
            pid = PLATFORM_ROUTER_ID.get(platform, 0)
            user_addr = W3.to_checksum_address(user_wallet)
            metadata = W3.to_bytes(text=json.dumps({"sell_id": sell_id, "platform": platform}))
            tx_params = {
                "from": OWNER_ACCOUNT.address,
                "nonce": chain_w3.eth.get_transaction_count(OWNER_ACCOUNT.address, "pending"),
                "gas": 200000,
                "chainId": chain_id,
            }
            if chain_id == 8453:
                tx_params["maxFeePerGas"] = chain_w3.eth.gas_price * 2
                tx_params["maxPriorityFeePerGas"] = chain_w3.eth.max_priority_fee
            else:
                tx_params["gasPrice"] = chain_w3.eth.gas_price
            tx = router.functions.transferERC1155(
                W3.to_checksum_address(ctf_address), user_addr, pid,
                int(token_id), shares_to_sell, metadata,
            ).build_transaction(tx_params)
            signed = OWNER_ACCOUNT.sign_transaction(tx)
            tx_hash = chain_w3.eth.send_raw_transaction(signed.raw_transaction)
            pull_tx = "0x" + tx_hash.hex()
            receipt = chain_w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt["status"] != 1:
                raise Exception(f"pull tx reverted: {pull_tx}")
            logger.info(f"Sell {sell_id}: Router.transferERC1155 on chain {chain_id}, pulled {shares_to_sell} shares, tx={pull_tx}")
        except Exception as e:
            return {"error": f"failed to pull shares: {e}"}

    sell_order = {
        "id": sell_id,
        "direction": "sell",
        "buy_order_id": buy_order_id,
        "wallet": user_wallet,
        "event_id": buy_order.get("event_id"),
        "team": buy_order.get("team"),
        "side": buy_order.get("side"),
        "platforms": {platform: {"token_id": token_id, "market_id": market_id}},
        "shares_amount": shares_to_sell,
        "pull_tx": pull_tx,
        "to_chain": body.get("to_chain", 8453),
        "status": "shares_pulled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _orders_lock:
        orders = _load_orders()
        orders.append(sell_order)
        _save_orders(orders)
    return sell_order


# ---- LiFi Status Poller + Trade Executor ----

def _execute_trades(order: dict) -> dict:
    """Place orders on prediction markets for a bridged order."""
    platforms = order.get("platforms", {})
    side_map = {"yes": "BUY", "no": "BUY"}  # buying outcome tokens
    results = {}

    for pname, pdata in platforms.items():
        token_id = pdata.get("token_id")
        market_id = pdata.get("market_id")
        spent = pdata.get("spent", 0)
        if not token_id or spent <= 0:
            continue

        if pname == "polymarket":
            adapter = _get_poly_adapter()
            if not adapter:
                results[pname] = {"error": "adapter not configured"}
                continue
            try:
                # Use actual USDC.e balance (bridge takes fees)
                from web3 import Web3 as W3
                _w3 = W3(W3.HTTPProvider(POLYGON_RPC))
                _usdc_abi = [{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "type": "function"}]
                _usdc = _w3.eth.contract(address=W3.to_checksum_address(USDC_POLYGON), abi=_usdc_abi)
                relayer_addr = Account.from_key(RELAYER_KEY).address
                actual_balance = _usdc.functions.balanceOf(relayer_addr).call() / 1e6
                # Floor to 2 decimals (USDC cents)
                actual_spent = math.floor(min(spent, actual_balance) * 100) / 100
                if actual_spent < 1.0:
                    results[pname] = {"error": f"insufficient USDC.e: {actual_balance:.4f}, min $1"}
                    continue
                logger.info(f"Order {order['id']}: budget={spent}, actual USDC.e={actual_balance:.4f}, using={actual_spent:.2f}")
                # Get best ask price
                best = adapter.get_best_offer(token_id, "BUY")
                price = best["price"]
                if price <= 0:
                    results[pname] = {"error": "no asks available"}
                    continue
                # amount = shares to buy (FOK sweeps across levels)
                amount = math.floor((actual_spent / price) * 100) / 100
                logger.info(f"Order {order['id']}: {pname} amount={amount} shares @ {price}")
                resp = adapter.place_order(
                    token_id=token_id,
                    market_id=int(market_id) if market_id else 0,
                    amount=amount,
                    price=price,
                    side="BUY",
                )
                results[pname] = {
                    "order_id": resp.get("orderID") or resp.get("orderId"),
                    "status": resp.get("status"),
                    "order_params": resp.get("_params", {}),
                }
                logger.info(f"Order {order['id']}: {pname} placed, status={resp.get('status')}")
            except Exception as e:
                logger.error(f"Order {order['id']}: {pname} trade failed: {e}")
                results[pname] = {"error": str(e)}
        elif pname == "opinion":
            adapter = _get_opinion_adapter()
            if not adapter:
                results[pname] = {"error": "adapter not configured"}
                continue
            try:
                # Check actual USDT balance on smart wallet (18 decimals)
                actual_balance = adapter.get_usdt_balance() / 1e18
                actual_spent = math.floor(min(spent, actual_balance) * 100) / 100
                if actual_spent < 1.0:
                    results[pname] = {"error": f"insufficient USDT: {actual_balance:.4f}, min $1"}
                    continue
                logger.info(f"Order {order['id']}: budget={spent}, actual USDT={actual_balance:.4f}, using={actual_spent:.2f}")
                best = adapter.get_best_offer(token_id, "BUY")
                price = best["price"]
                if price <= 0:
                    results[pname] = {"error": "no asks available"}
                    continue
                # Opinion BUY: amount = USDT to spend (makerAmountInQuoteToken)
                resp = adapter.place_order(
                    token_id=token_id,
                    market_id=int(market_id) if market_id else 0,
                    amount=actual_spent, price=price, side="BUY",
                )
                results[pname] = {
                    "order_id": resp.get("orderId"),
                    "status": resp.get("status"),
                    "price": price,
                    "amount": actual_spent,
                }
                logger.info(f"Order {order['id']}: {pname} placed, status={resp.get('status')}")
            except Exception as e:
                logger.error(f"Order {order['id']}: {pname} trade failed: {e}")
                results[pname] = {"error": str(e)}
        elif pname == "limitless":
            adapter = _get_limitless_adapter()
            if not adapter:
                results[pname] = {"error": "adapter not configured"}
                continue
            try:
                # Limitless is on Base, USDC already on relayer (transferred in create_order)
                actual_balance = adapter.get_usdc_balance() / 1e6
                actual_spent = math.floor(min(spent, actual_balance) * 100) / 100
                if actual_spent < 1.0:
                    results[pname] = {"error": f"insufficient USDC: {actual_balance:.4f}, min $1"}
                    continue
                logger.info(f"Order {order['id']}: budget={spent}, actual USDC={actual_balance:.4f}, using={actual_spent:.2f}")
                best = adapter.get_best_offer(market_id, "BUY")  # slug as token_id for orderbook
                price = best["price"]
                if price <= 0:
                    results[pname] = {"error": "no asks available"}
                    continue
                # BUY: amount = USDC to spend
                resp = adapter.place_order(
                    token_id=token_id,
                    market_id=market_id,  # slug
                    amount=actual_spent, price=price, side="BUY",
                )
                results[pname] = {
                    "order_id": resp.get("orderId"),
                    "status": resp.get("status"),
                    "price": price,
                    "amount": actual_spent,
                }
                logger.info(f"Order {order['id']}: {pname} placed, status={resp.get('status')}")
            except Exception as e:
                logger.error(f"Order {order['id']}: {pname} trade failed: {e}")
                results[pname] = {"error": str(e)}
        else:
            results[pname] = {"error": "adapter not implemented"}

    return results


def _settle_and_transfer(order: dict) -> dict:
    """Check if shares settled on relayer, transfer to user. 5 retries, 5s apart."""
    trade_results = order.get("trade_results", {})
    user_wallet = order.get("wallet")
    if not user_wallet:
        return {"done": False}

    transfers = {}

    for pname, tdata in trade_results.items():
        if "error" in tdata:
            continue
        token_id = order.get("platforms", {}).get(pname, {}).get("token_id")
        if not token_id:
            continue
        if tdata.get("transfer_tx"):
            continue

        if pname == "polymarket":
            adapter = _get_poly_adapter()
            if not adapter:
                continue
            # Retry up to 5 times, 5s apart
            for attempt in range(5):
                balance = adapter.get_shares_balance(token_id)
                if balance > 0:
                    result = adapter.transfer_shares(token_id, user_wallet, balance)
                    transfers[pname] = {
                        "tx_hash": result["tx_hash"],
                        "success": result["success"],
                        "amount": balance,
                    }
                    logger.info(f"Order {order['id']}: {pname} transferred {balance} shares, attempt {attempt+1}")
                    break
                logger.info(f"Order {order['id']}: {pname} settlement pending, attempt {attempt+1}/5")
                if attempt < 4:
                    time.sleep(5)
            else:
                logger.warning(f"Order {order['id']}: {pname} settlement not received after 5 retries")

        elif pname == "opinion":
            adapter = _get_opinion_adapter()
            if not adapter:
                continue
            # Opinion API tracks balances internally — keep shares on smart wallet
            # so sell flow can use them without re-indexing issues
            for attempt in range(5):
                balance = adapter.get_shares_balance(token_id)
                if balance > 0:
                    # Don't transfer to user — record as "settled on smart wallet"
                    transfers[pname] = {"tx_hash": "kept_on_smart_wallet", "success": True, "amount": balance}
                    logger.info(f"Order {order['id']}: opinion shares kept on smart_wallet, balance={balance}")
                    break
                logger.info(f"Order {order['id']}: opinion settlement pending, attempt {attempt+1}/5")
                if attempt < 4:
                    time.sleep(5)
            else:
                logger.warning(f"Order {order['id']}: opinion settlement not received after 5 retries")

        elif pname == "limitless":
            adapter = _get_limitless_adapter()
            if not adapter:
                continue
            for attempt in range(5):
                balance = adapter.get_shares_balance(token_id)
                if balance > 0:
                    result = adapter.transfer_shares(token_id, user_wallet, balance)
                    transfers[pname] = {"tx_hash": result["tx_hash"], "success": result["success"], "amount": balance}
                    logger.info(f"Order {order['id']}: limitless transferred {balance} shares, attempt {attempt+1}")
                    break
                logger.info(f"Order {order['id']}: limitless settlement pending, attempt {attempt+1}/5")
                if attempt < 4:
                    time.sleep(5)
            else:
                logger.warning(f"Order {order['id']}: limitless settlement not received after 5 retries")

    all_ok = all(t.get("success") for t in transfers.values()) and len(transfers) > 0
    return {"done": all_ok, "transfers": transfers}


# ---- Sell flow helpers ----

def _execute_sell(order: dict) -> dict:
    """Sell shares on platform CLOB. Returns trade results."""
    # Detect platform
    platform = next(iter(order.get("platforms", {})), "polymarket")
    pdata = order["platforms"][platform]
    token_id = pdata["token_id"]
    market_id = pdata.get("market_id")
    shares = order["shares_amount"]
    decimals = PLATFORM_DECIMALS.get(platform, 6)

    adapter = _get_adapter(platform)
    if not adapter:
        return {"error": f"{platform} adapter not configured"}

    try:
        # For limitless, orderbook uses slug (market_id) not token_id
        ob_key = market_id if platform == "limitless" else token_id
        best = adapter.get_best_offer(ob_key, "SELL")
        price = best["price"]
        if price <= 0:
            return {"error": "no bids available"}

        # Convert raw shares to human-readable
        amount = math.floor((shares / (10 ** decimals)) * 100) / 100
        # Check platform minimums (order value = amount * price)
        order_value = amount * price
        min_value = {"opinion": 1.30}.get(platform, 1.0)
        if order_value < min_value:
            return {"error": f"order value ${order_value:.2f} below {platform} minimum ${min_value:.2f}"}

        # Debug: check on-chain balance of trading wallet before sell
        if platform == "opinion":
            # Wait for BSC indexing
            time.sleep(5)
            sw_bal = adapter.get_shares_balance(token_id)
            logger.info(f"Sell {order['id']}: Opinion smart_wallet={adapter.smart_wallet} shares={sw_bal}, need={shares} ({amount})")
            if sw_bal <= 0:
                eoa_bal = adapter.get_token_balance(adapter._main_relayer_address, token_id)
                user_bal = adapter.get_user_shares_balance(token_id, order["wallet"])
                logger.info(f"Sell {order['id']}: main_relayer={adapter._main_relayer_address} eoa_bal={eoa_bal}, user_bal={user_bal}")
                return {"error": f"Smart wallet has 0 shares (sw={adapter.smart_wallet}, eoa={eoa_bal}, user={user_bal}). Pull tx: {order.get('pull_tx')}"}

        # Snapshot balance BEFORE placing order
        get_bal = adapter.get_usdt_balance if platform == "opinion" else adapter.get_usdc_balance
        balance_before = get_bal()

        # Limitless uses slug as market_id, others use int
        mid = market_id if platform == "limitless" else (int(market_id) if market_id else 0)
        resp = adapter.place_order(
            token_id=token_id,
            market_id=mid,
            amount=amount, price=price, side="SELL",
        )
        logger.info(f"Sell {order['id']}: placed SELL on {platform}, status={resp.get('status')}, balance_before={balance_before}")
        return {
            "order_id": resp.get("orderID") or resp.get("orderId"),
            "status": resp.get("status"),
            "price": price,
            "amount": amount,
            "balance_before": balance_before,
            "order_params": resp.get("_params", {}),
        }
    except Exception as e:
        logger.error(f"Sell {order['id']}: SELL failed on {platform}: {e}")
        return {"error": str(e)}


def _settle_sell(order: dict) -> dict:
    """Wait for sell settlement: balance must increase above pre-order snapshot. 5 retries, 5s apart."""
    platform = next(iter(order.get("platforms", {})), "polymarket")
    adapter = _get_adapter(platform)
    if not adapter:
        return {"done": False}

    decimals = PLATFORM_DECIMALS.get(platform, 6)
    trade = order.get("trade_results", {}).get(platform, {})
    balance_before = trade.get("balance_before", 0)
    get_bal = adapter.get_usdt_balance if platform == "opinion" else adapter.get_usdc_balance

    for attempt in range(5):
        balance = get_bal()
        if balance > balance_before:
            proceeds = balance - balance_before
            logger.info(f"Sell {order['id']}: settled, before={balance_before}, after={balance}, proceeds={proceeds / (10 ** decimals):.4f}")
            return {"done": True, "balance_before": balance_before, "balance_after": balance, "proceeds": proceeds}
        logger.info(f"Sell {order['id']}: waiting for settlement, attempt {attempt+1}/5, balance={balance}, need > {balance_before}")
        if attempt < 4:
            time.sleep(5)

    balance = get_bal()
    if balance > balance_before:
        proceeds = balance - balance_before
        return {"done": True, "balance_before": balance_before, "balance_after": balance, "proceeds": proceeds}
    return {"done": False}


def _bridge_back(order: dict) -> dict:
    """Bridge stablecoin to user's chosen chain (or direct transfer if same chain)."""
    platform = next(iter(order.get("platforms", {})), "polymarket")
    adapter = _get_adapter(platform)
    if not adapter:
        return {"error": f"{platform} adapter not configured"}

    user_wallet = order["wallet"]
    user_addr = Web3.to_checksum_address(user_wallet)
    decimals = PLATFORM_DECIMALS.get(platform, 6)
    from_chain = PLATFORM_CHAIN.get(platform, 137)
    from_token = PLATFORM_STABLE.get(platform, USDC_POLYGON)

    # User's chosen destination chain (default = Base for backward compat)
    to_chain = order.get("to_chain", 8453)
    to_token = CHAIN_STABLE.get(to_chain, USDC_BASE)

    settle = order.get("settle_results", {})
    proceeds = settle.get("proceeds", 0)
    if proceeds <= 0:
        return {"error": "no sell proceeds to bridge"}

    # Same chain — direct stablecoin transfer, no bridge
    if from_chain == to_chain:
        try:
            tx_hash = adapter.transfer_usdt_to_user(user_addr, proceeds)
            if from_chain == 8453:
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            else:
                from web3 import Web3 as W3
                rpc_map = {137: POLYGON_RPC, 56: BSC_RPC}
                W3(W3.HTTPProvider(rpc_map.get(from_chain, POLYGON_RPC))).eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            logger.info(f"Sell {order['id']}: same-chain transfer {proceeds/(10**decimals):.4f} to user on chain {from_chain}, tx={tx_hash}")
            return {"bridge_tx": tx_hash, "amount": proceeds}
        except Exception as e:
            return {"error": str(e)}

    # For Opinion: transfer only proceeds from smart wallet to main relayer
    if platform == "opinion":
        adapter.transfer_usdt_to_user(adapter._main_relayer_address, proceeds)
        time.sleep(3)
        from_address = adapter._main_relayer_address
        bridge_key = RELAYER_KEY
        balance = proceeds
    else:
        balance = proceeds
        from_address = Account.from_key(RELAYER_KEY).address
        bridge_key = RELAYER_KEY

    # Floor to 2 decimal places
    floor_factor = 10 ** (decimals - 2)
    amount_raw = int(math.floor(balance / floor_factor) * floor_factor)
    min_amount = 10 ** decimals  # $1
    if amount_raw < min_amount:
        # Too small for LiFi — fallback: direct transfer on platform chain
        # Check actual balance first to avoid stealing other orders' funds
        actual_bal = adapter.get_stablecoin_balance() if hasattr(adapter, 'get_stablecoin_balance') else proceeds
        if actual_bal < proceeds:
            return {"error": f"insufficient balance for fallback: have {actual_bal/(10**decimals):.4f}, need {proceeds/(10**decimals):.4f}"}
        try:
            tx_hash = adapter.transfer_usdt_to_user(user_addr, proceeds)
            rpc_map = {137: POLYGON_RPC, 56: BSC_RPC, 8453: BASE_RPC}
            from web3 import Web3 as W3
            _w = W3(W3.HTTPProvider(rpc_map.get(from_chain, POLYGON_RPC)))
            _w.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            logger.info(f"Sell {order['id']}: amount too small for bridge, direct transfer {proceeds/(10**decimals):.4f} on chain {from_chain}, tx={tx_hash}")
            return {"bridge_tx": tx_hash, "amount": proceeds, "direct": True}
        except Exception as e:
            return {"error": f"fallback transfer failed: {e}"}

    try:
        lifi_resp = req_lib.get("https://li.quest/v1/quote", params={
            "fromChain": from_chain,
            "toChain": to_chain,
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": str(amount_raw),
            "fromAddress": from_address,
            "toAddress": user_addr,
            "slippage": "0.05",
            "integrator": "premarket-router",
        }, timeout=15)
        lifi_quote = lifi_resp.json()
        if "transactionRequest" not in lifi_quote:
            return {"error": f"LiFi quote failed: {lifi_quote}"}

        tx_req = lifi_quote["transactionRequest"]
        lifi_to = Web3.to_checksum_address(tx_req["to"])
        lifi_data = tx_req["data"]
        lifi_value = int(tx_req.get("value", "0"), 16) if isinstance(tx_req.get("value"), str) else int(tx_req.get("value", 0))

        # Connect to source chain
        from web3 import Web3 as W3
        rpc_map = {8453: BASE_RPC, 137: POLYGON_RPC, 56: BSC_RPC}
        w3_src = W3(W3.HTTPProvider(rpc_map.get(from_chain, BASE_RPC)))
        if from_chain == 137:
            from web3.middleware import ExtraDataToPOAMiddleware
            w3_src.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # Approve LiFi diamond
        approve_abi = [{"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                        "name": "approve", "outputs": [{"type": "bool"}], "type": "function"}]
        token_contract = w3_src.eth.contract(address=W3.to_checksum_address(from_token), abi=approve_abi)
        gas_price = w3_src.eth.gas_price
        nonce = w3_src.eth.get_transaction_count(from_address, "pending")
        approve_tx = token_contract.functions.approve(lifi_to, amount_raw).build_transaction({
            "from": from_address,
            "nonce": nonce,
            "gas": 80000,
            "gasPrice": int(gas_price * 1.3),
            "chainId": from_chain,
        })
        signed_approve = w3_src.eth.account.sign_transaction(approve_tx, bridge_key)
        w3_src.eth.send_raw_transaction(signed_approve.raw_transaction)
        w3_src.eth.wait_for_transaction_receipt(signed_approve.hash, timeout=60)
        nonce += 1
        logger.info(f"Sell {order['id']}: approved LiFi on chain {from_chain}")

        # Send bridge tx — use gasLimit from LiFi
        lifi_gas_raw = tx_req.get("gasLimit", "0")
        lifi_gas = int(lifi_gas_raw, 16) if isinstance(lifi_gas_raw, str) and lifi_gas_raw.startswith("0x") else int(lifi_gas_raw or 0)
        if lifi_gas < 500000:
            lifi_gas = 800000
        bridge_tx = {
            "from": from_address, "to": lifi_to, "data": lifi_data, "value": lifi_value,
            "nonce": nonce,
            "gas": lifi_gas, "gasPrice": int(gas_price * 1.5), "chainId": from_chain,
        }
        signed_bridge = w3_src.eth.account.sign_transaction(bridge_tx, bridge_key)
        bridge_hash = w3_src.eth.send_raw_transaction(signed_bridge.raw_transaction)
        receipt = w3_src.eth.wait_for_transaction_receipt(bridge_hash, timeout=120)
        h = "0x" + bridge_hash.hex() if not bridge_hash.hex().startswith("0x") else bridge_hash.hex()

        if receipt["status"] != 1:
            return {"error": f"bridge tx reverted: {h}"}

        logger.info(f"Sell {order['id']}: bridge tx from {from_chain} to {to_chain}, hash={h}")
        return {"bridge_tx": h, "amount": amount_raw}

    except Exception as e:
        logger.error(f"Sell {order['id']}: bridge back failed: {e}")
        return {"error": str(e)}


async def poll_orders():
    """Background task: poll LiFi status for sent orders, execute trades for bridged."""
    MAX_RETRIES = 5
    while True:
        await asyncio.sleep(10)
        orders = _load_orders()
        changed = False

        changed_ids = set()

        for o in orders:
            # Skip killed/terminal orders
            if o.get("status") == "killed":
                continue

            # === BUY FLOW ===
            # Step 1: Poll LiFi for sent orders (supports multi-bridge)
            if o["status"] == "sent":
                try:
                    bridges = o.get("bridges")
                    if bridges:
                        # Multi-bridge: poll each bridge independently
                        all_done = True
                        any_failed = False
                        for chain_id, bdata in bridges.items():
                            if bdata.get("status") == "done":
                                continue
                            btx = bdata.get("bridge_tx")
                            if not btx:
                                continue
                            async with httpx.AsyncClient() as client:
                                resp = await client.get("https://li.quest/v1/status", params={"txHash": btx}, timeout=10)
                                data = resp.json()
                                st = data.get("status", "")
                                if st == "DONE":
                                    bdata["status"] = "done"
                                    logger.info(f"Order {o['id']}: bridge to chain {chain_id} done")
                                elif st == "FAILED":
                                    bdata["status"] = "failed"
                                    any_failed = True
                                    logger.warning(f"Order {o['id']}: bridge to chain {chain_id} failed")
                                else:
                                    all_done = False
                        if any_failed:
                            o["status"] = "failed"
                            o["error"] = "one or more bridges failed"
                            changed = True; changed_ids.add(o["id"])
                        elif all_done:
                            o["status"] = "bridged"
                            changed = True; changed_ids.add(o["id"])
                            logger.info(f"Order {o['id']}: all bridges done")
                    elif o.get("bridge_tx") or o.get("tx_hash"):
                        # Legacy single-bridge: poll as before
                        poll_tx = o.get("bridge_tx") or o["tx_hash"]
                        async with httpx.AsyncClient() as client:
                            resp = await client.get("https://li.quest/v1/status", params={"txHash": poll_tx}, timeout=10)
                            data = resp.json()
                            lifi_status = data.get("status", "")
                            if lifi_status == "DONE":
                                o["status"] = "bridged"
                                recv = data.get("receiving", {})
                                o["receiving_tx_hash"] = recv.get("txHash")
                                o["receiving_chain_id"] = recv.get("chainId")
                                changed = True; changed_ids.add(o["id"])
                                logger.info(f"Order {o['id']}: bridge done")
                            elif lifi_status == "FAILED":
                                o["status"] = "failed"
                                changed = True; changed_ids.add(o["id"])
                except Exception:
                    pass

            # Step 2: Execute trades for bridged orders (with retries)
            elif o["status"] in ("bridged", "trade_failed") and o.get("direction") != "sell":
                retries = o.get("trade_retries", 0)
                if o["status"] == "trade_failed" and (retries >= MAX_RETRIES or "trade_retries" not in o):
                    continue
                try:
                    results = await asyncio.to_thread(_execute_trades, o)
                    o["trade_results"] = results
                    all_ok = all("error" not in v for v in results.values()) and len(results) > 0
                    if all_ok:
                        o["status"] = "matched"
                        logger.info(f"Order {o['id']}: trades matched on attempt {retries+1}")
                    else:
                        o["trade_retries"] = retries + 1
                        o["trade_error"] = str({k: v.get("error") for k, v in results.items() if "error" in v})
                        if retries + 1 >= MAX_RETRIES:
                            o["status"] = "trade_failed"
                        logger.warning(f"Order {o['id']}: trade attempt {retries+1}/{MAX_RETRIES} failed")
                    changed = True; changed_ids.add(o["id"])
                except Exception as e:
                    o["trade_retries"] = retries + 1
                    o["trade_error"] = str(e)
                    if retries + 1 >= MAX_RETRIES:
                        o["status"] = "trade_failed"
                    changed = True; changed_ids.add(o["id"])
                    logger.error(f"Order {o['id']}: trade attempt {retries+1}/{MAX_RETRIES} error: {e}")

            # Step 3: Poll settlement + transfer shares to user (with retries)
            elif o["status"] == "matched" and o.get("direction") != "sell":
                retries = o.get("settle_retries", 0)
                try:
                    result = await asyncio.to_thread(_settle_and_transfer, o)
                    if result.get("done"):
                        o["transfer_results"] = result.get("transfers", {})
                        o["status"] = "filled"
                        changed = True; changed_ids.add(o["id"])
                        logger.info(f"Order {o['id']}: shares transferred to user")
                    else:
                        o["settle_retries"] = retries + 1
                        if retries + 1 >= MAX_RETRIES * 2:
                            o["status"] = "trade_failed"
                            o["trade_error"] = "settlement timeout"
                            changed = True; changed_ids.add(o["id"])
                        logger.info(f"Order {o['id']}: settle attempt {retries+1}, waiting…")
                except Exception as e:
                    o["settle_retries"] = retries + 1
                    logger.error(f"Order {o['id']}: settlement check error: {e}")

            # === SELL FLOW ===

            # Sell step 1: shares_pulled -> sell on platform (with retries)
            elif o["status"] in ("shares_pulled", "trade_failed") and o.get("direction") == "sell":
                retries = o.get("trade_retries", 0)
                if retries >= MAX_RETRIES or (o["status"] == "trade_failed" and "trade_retries" not in o):
                    continue
                try:
                    sell_platform = next(iter(o.get("platforms", {})), "polymarket")
                    result = await asyncio.to_thread(_execute_sell, o)
                    o["trade_results"] = {sell_platform: result}
                    if "error" in result:
                        o["trade_retries"] = retries + 1
                        o["trade_error"] = result["error"]
                        if retries + 1 >= MAX_RETRIES:
                            o["status"] = "trade_failed"
                        # else stay in same status for retry
                        logger.warning(f"Sell {o['id']}: trade attempt {retries+1}/{MAX_RETRIES} failed: {result['error']}")
                    else:
                        o["status"] = "sell_matched"
                        logger.info(f"Sell {o['id']}: matched on attempt {retries+1}")
                    changed = True; changed_ids.add(o["id"])
                except Exception as e:
                    o["trade_retries"] = retries + 1
                    o["trade_error"] = str(e)
                    if retries + 1 >= MAX_RETRIES:
                        o["status"] = "trade_failed"
                    changed = True; changed_ids.add(o["id"])
                    logger.error(f"Sell {o['id']}: trade attempt {retries+1}/{MAX_RETRIES} error: {e}")

            # Sell step 2: sell_matched -> wait for USDC settlement (with retries)
            elif o["status"] == "sell_matched" and o.get("direction") == "sell":
                retries = o.get("settle_retries", 0)
                try:
                    result = await asyncio.to_thread(_settle_sell, o)
                    if result.get("done"):
                        o["settle_results"] = result
                        o["status"] = "sell_settled"
                        changed = True; changed_ids.add(o["id"])
                        logger.info(f"Sell {o['id']}: settled")
                    else:
                        o["settle_retries"] = retries + 1
                        if retries + 1 >= MAX_RETRIES * 2:
                            o["status"] = "trade_failed"
                            o["trade_error"] = "settlement timeout"
                            changed = True; changed_ids.add(o["id"])
                        logger.info(f"Sell {o['id']}: settle attempt {retries+1}, waiting…")
                except Exception as e:
                    o["settle_retries"] = retries + 1
                    logger.error(f"Sell {o['id']}: settle error: {e}")

            # Sell step 3: sell_settled -> bridge back (with retries)
            elif o["status"] in ("sell_settled", "bridge_failed") and o.get("direction") == "sell":
                retries = o.get("bridge_retries", 0)
                if retries >= MAX_RETRIES:
                    continue
                try:
                    sell_platform = next(iter(o.get("platforms", {})), "polymarket")
                    result = await asyncio.to_thread(_bridge_back, o)
                    if "error" in result:
                        o["bridge_retries"] = retries + 1
                        o["bridge_error"] = result["error"]
                        if retries + 1 >= MAX_RETRIES:
                            o["status"] = "bridge_failed"
                        logger.warning(f"Sell {o['id']}: bridge attempt {retries+1}/{MAX_RETRIES} failed: {result['error']}")
                    else:
                        o["bridge_back_tx"] = result["bridge_tx"]
                        o["bridge_back_amount"] = result["amount"]
                        sell_from_chain = PLATFORM_CHAIN.get(sell_platform, 137)
                        sell_to_chain = o.get("to_chain", 8453)
                        if result.get("direct") or sell_from_chain == sell_to_chain:
                            o["status"] = "completed"
                        else:
                            o["status"] = "bridging_back"
                        logger.info(f"Sell {o['id']}: bridge sent on attempt {retries+1}")
                    changed = True; changed_ids.add(o["id"])
                except Exception as e:
                    o["bridge_retries"] = retries + 1
                    o["bridge_error"] = str(e)
                    if retries + 1 >= MAX_RETRIES:
                        o["status"] = "bridge_failed"
                    changed = True; changed_ids.add(o["id"])
                    logger.error(f"Sell {o['id']}: bridge attempt {retries+1}/{MAX_RETRIES} error: {e}")

            # Sell step 4: bridging_back -> poll LiFi status
            elif o["status"] == "bridging_back" and o.get("direction") == "sell":
                try:
                    tx_hash = o.get("bridge_back_tx")
                    if tx_hash:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(
                                "https://li.quest/v1/status",
                                params={"txHash": tx_hash},
                                timeout=10,
                            )
                            data = resp.json()
                            lifi_status = data.get("status", "")
                            if lifi_status == "DONE":
                                recv = data.get("receiving", {})
                                o["receiving_tx_hash"] = recv.get("txHash")
                                o["receiving_chain_id"] = recv.get("chainId")
                                o["status"] = "completed"
                                changed = True; changed_ids.add(o["id"])
                                logger.info(f"Sell {o['id']}: bridge back done, completed")
                            elif lifi_status == "FAILED":
                                o["status"] = "bridge_failed"
                                o["bridge_retries"] = 0  # allow retry of bridge
                                changed = True; changed_ids.add(o["id"])
                except Exception:
                    pass

        if changed:
            with _orders_lock:
                fresh = _load_orders()
                fresh_map = {o["id"]: o for o in fresh}
                # Only merge orders that were actually changed in this cycle
                for o in orders:
                    if o["id"] in changed_ids:
                        fresh_map[o["id"]] = o
                _save_orders(list(fresh_map.values()))


@app.post("/api/kill-order/{order_id}")
async def kill_order(order_id: str):
    with _orders_lock:
        orders = _load_orders()
        for o in orders:
            if o["id"] == order_id:
                o["status"] = "killed"
                o["trade_retries"] = 99
                o["bridge_retries"] = 99
                _save_orders(orders)
                return {"ok": True, "id": order_id}
        return {"error": "not found"}

@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_orders())
