"""Pure CSFloat buy-order price and recent-sale analysis."""

from __future__ import annotations


def csfloat_buy_increment_cents(price_cents):
    """Return CSFloat's current FAQ price step for a USD-cent value."""
    try:
        price = max(0, int(price_cents or 0))
    except (TypeError, ValueError):
        price = 0
    if price < 500:
        return 1
    if price < 1000:
        return 5
    if price < 10000:
        return 10
    if price < 50000:
        return 100
    if price < 100000:
        return 500
    return 1000


def csfloat_next_legal_buy_price(price_cents):
    """Return the smallest tier-aligned price strictly above ``price_cents``."""
    try:
        candidate = max(0, int(price_cents or 0)) + 1
    except (TypeError, ValueError):
        candidate = 1
    for _ in range(4):
        step = csfloat_buy_increment_cents(candidate)
        rounded = ((candidate + step - 1) // step) * step
        if rounded == candidate and csfloat_buy_increment_cents(rounded) == step:
            return rounded
        candidate = rounded
    return candidate


def analyze_csfloat_buy_order(own_price_cents, market_price_cents, sales):
    """Summarize bid position and whether recent sales traded near the bid."""
    try:
        own_price = max(0, int(own_price_cents or 0))
    except (TypeError, ValueError):
        own_price = 0
    try:
        market_price = max(0, int(market_price_cents or 0))
    except (TypeError, ValueError):
        market_price = 0

    valid_sales = []
    for sale in sales or []:
        if not isinstance(sale, dict):
            continue
        try:
            price = int(sale.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0 and own_price > 0:
            valid_sales.append({
                "price": price,
                "sold_at": str(sale.get("sold_at") or ""),
                "gap_percent": abs(price - own_price) / own_price * 100,
                "signed_gap_percent": (price - own_price) / own_price * 100,
            })

    nearest_sale = min(valid_sales, key=lambda value: value["gap_percent"], default=None)
    within_2 = sum(value["gap_percent"] <= 2.0 for value in valid_sales)
    within_5 = sum(value["gap_percent"] <= 5.0 for value in valid_sales)
    if within_2:
        purchase_signal = "较强"
        signal_color = "#a6e3a1"
    elif within_5:
        purchase_signal = "一般"
        signal_color = "#f9e2af"
    elif valid_sales:
        purchase_signal = "偏弱"
        signal_color = "#f38ba8"
    else:
        purchase_signal = "暂无样本"
        signal_color = "#6c7086"

    if market_price <= 0:
        price_status = "市场最高价未知"
        gap_cents = None
        target_price = None
        at_top = False
    elif own_price >= market_price:
        price_status = "最高价位"
        gap_cents = 0
        target_price = own_price
        at_top = True
    else:
        gap_cents = market_price - own_price
        target_price = csfloat_next_legal_buy_price(market_price)
        price_status = f"落后 ${gap_cents / 100:.2f}"
        at_top = False

    return {
        "own_price_cents": own_price,
        "market_price_cents": market_price,
        "price_status": price_status,
        "gap_cents": gap_cents,
        "target_price_cents": target_price,
        "at_top": at_top,
        "sales_count": len(valid_sales),
        "within_2_percent": within_2,
        "within_5_percent": within_5,
        "nearest_sale": nearest_sale,
        "purchase_signal": purchase_signal,
        "signal_color": signal_color,
    }
