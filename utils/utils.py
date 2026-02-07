def build_pooled(books: list[dict], side_key: str) -> list[dict]:
    """Merge liquidity from multiple orderbooks into a single pooled book."""
    # Price grid: 0.1 to 99.9 step 0.1 (in cents)
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

    # Remove empty levels
    result = []
    for key in sorted(grid.keys()):
        if grid[key] > 0:
            price = key / 10
            price_dec = price / 100
            size = round(grid[key], 2)
            result.append({
                "price": round(price_dec, 4),
                "size": size,
                "total": round(price_dec * size, 2),
                "price_cents": round(price, 1),
            })
    return result
