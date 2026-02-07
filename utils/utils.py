def build_pooled(books: list[dict], side_key: str) -> list[dict]:
    """Merge liquidity from multiple orderbooks into a single pooled book."""
    grid = {}
    for i in range(1, 1000):
        grid[i] = 0.0

    for book in books:
        if "error" in book:
            continue
        for level in book.get(side_key, []):
            price_key = round(level["price_cents"] * 10)
            if price_key in grid:
                grid[price_key] += level["size"]

    result = []
    cumsum = 0
    for key in sorted(grid.keys()):
        if grid[key] > 0:
            price = key / 10
            price_dec = price / 100
            size = round(grid[key], 2)
            total = round(price_dec * size, 2)
            cumsum += total
            result.append({
                "price": round(price_dec, 4),
                "size": size,
                "total": total,
                "price_cents": round(price, 1),
                "cumsum": round(cumsum, 2),
            })
    return result


def find_optimal_route(
    full_books: list[dict],
    budget: float,
    direction: str = "buy",
) -> dict:
    """Find optimal purchase distribution across orderbooks.

    Algorithm:
    1. Collect all price levels from every platform, tagged with source.
    2. Sort by price (ascending for buy/asks, descending for sell/bids).
    3. Walk through levels greedily — at same price prefer already-used sources
       to minimize total number of platforms used.
    4. Consume liquidity until budget is exhausted.

    Args:
        full_books: list of dicts with keys {platform, asks, bids}.
        budget: USDC amount to spend (buy) or shares to sell (sell).
        direction: 'buy' or 'sell'.

    Returns:
        dict with route details.
    """
    if budget <= 0:
        return {"error": "Budget must be > 0"}

    # Collect tagged levels
    side_key = "asks" if direction == "buy" else "bids"
    levels = []
    for book in full_books:
        platform = book["platform"]
        for lv in book.get(side_key, []):
            levels.append({
                "platform": platform,
                "price": lv["price"],
                "size": lv["size"],
                "price_cents": lv["price_cents"],
            })

    if not levels:
        return {"error": "No liquidity available"}

    # Sort: buy -> cheapest first, sell -> most expensive first
    reverse = direction == "sell"
    # At same price, prefer sources already used -> handled during walk
    levels.sort(key=lambda x: x["price"], reverse=reverse)

    # Group levels by price (preserve order)
    from itertools import groupby
    grouped = []
    for price, grp in groupby(levels, key=lambda x: x["price"]):
        grouped.append((price, list(grp)))

    remaining = budget
    used_platforms = set()
    fills = []  # individual fills
    per_platform = {}  # platform -> {spent, qty}

    def consume(lv):
        nonlocal remaining
        if remaining <= 0:
            return
        p = lv["platform"]
        available_cost = lv["price"] * lv["size"]
        if direction == "buy":
            spend = min(remaining, available_cost)
            qty = spend / lv["price"] if lv["price"] > 0 else 0
        else:
            qty = min(remaining, lv["size"])
            spend = qty * lv["price"]
        if qty <= 0:
            return
        fills.append({
            "platform": p,
            "price": lv["price"],
            "price_cents": lv["price_cents"],
            "size": round(qty, 4),
            "cost": round(spend, 4),
        })
        if p not in per_platform:
            per_platform[p] = {"spent": 0.0, "qty": 0.0}
        per_platform[p]["spent"] += spend
        per_platform[p]["qty"] += qty
        used_platforms.add(p)
        remaining -= spend if direction == "buy" else qty

    for price, group in grouped:
        if remaining <= 0:
            break

        # 1) Consume from already-used platforms first
        known = [lv for lv in group if lv["platform"] in used_platforms]
        for lv in known:
            consume(lv)

        # 2) If still remaining, pick the single new platform with most liquidity
        if remaining > 0:
            new = [lv for lv in group if lv["platform"] not in used_platforms]
            if new:
                # Aggregate volume per new platform at this price
                vol = {}
                for lv in new:
                    vol.setdefault(lv["platform"], 0)
                    vol[lv["platform"]] += lv["price"] * lv["size"]
                best_new = max(vol, key=vol.get)
                for lv in new:
                    if lv["platform"] == best_new:
                        consume(lv)

    total_spent = sum(v["spent"] for v in per_platform.values())
    total_qty = sum(v["qty"] for v in per_platform.values())
    avg_price = total_spent / total_qty if total_qty > 0 else 0

    # Round and add per-platform avg price
    for p in per_platform:
        s = per_platform[p]["spent"]
        q = per_platform[p]["qty"]
        pp_avg = s / q if q > 0 else 0
        per_platform[p]["spent"] = round(s, 4)
        per_platform[p]["qty"] = round(q, 4)
        per_platform[p]["avg_price"] = round(pp_avg, 6)
        per_platform[p]["avg_price_cents"] = round(pp_avg * 100, 2)

    return {
        "direction": direction,
        "budget": budget,
        "total_spent": round(total_spent, 4),
        "total_qty": round(total_qty, 4),
        "avg_price": round(avg_price, 6),
        "avg_price_cents": round(avg_price * 100, 2),
        "unfilled": round(max(remaining, 0), 4),
        "platforms_used": len(per_platform),
        "per_platform": per_platform,
        "fills": fills,
    }
