"""Food-order routes — parsed Swiggy/Toing invoices for downstream apps (Cointrail)."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from modules.food.service import get_orders, process_food_orders

router = APIRouter()


@router.get("/orders")
def food_orders(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    platform: Optional[str] = None,
    restaurant: Optional[str] = None,
    limit: int = 500,
):
    """List parsed food orders (restaurant + itemized), newest first."""
    orders = get_orders(from_date, to_date, platform, restaurant, limit)
    return {"orders": orders, "count": len(orders)}


@router.post("/process")
def food_process(limit: Optional[int] = None, backfill: bool = False):
    """
    Parse food-invoice PDFs into food_orders.

    backfill=false (default) -> only emails not yet stored (hourly incremental).
    backfill=true            -> re-scan all food emails (idempotent via order_id).
    """
    try:
        stats = process_food_orders(limit=limit, only_new=not backfill)
        return {"status": "ok", "stats": stats}
    except FileNotFoundError as e:
        raise HTTPException(503, f"Gmail auth unavailable: {e}")
    except Exception as e:
        raise HTTPException(500, str(e))
