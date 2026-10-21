import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";

// Parsed food orders (Swiggy/Toing invoice PDFs) served by Ingest /food/orders.
// Each order carries the restaurant name and its itemized list.
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const from = searchParams.get("from") || undefined;
  const to = searchParams.get("to") || undefined;
  const platform = searchParams.get("platform") || undefined;
  const restaurant = searchParams.get("restaurant") || undefined;
  const limit = searchParams.get("limit") || "1000";

  try {
    const p = new URLSearchParams();
    if (from) p.set("from", from);
    if (to) p.set("to", to);
    if (platform) p.set("platform", platform);
    if (restaurant) p.set("restaurant", restaurant);
    p.set("limit", limit);

    const data = await ingestFetch(`/food/orders?${p.toString()}`);

    const orders = (data.orders || []).map((o: any) => ({
      order_id: o.order_id,
      platform: o.platform,
      order_type: "delivery",
      restaurant_name: o.restaurant,
      total_amount: o.total,
      order_date: o.order_date,
      item_count: o.item_count,
      // Exclude packing/fee lines from the displayed items; map amount -> price.
      items: (o.items || [])
        .filter((it: any) => !it.is_fee)
        .map((it: any) => ({ name: it.name, qty: it.qty, price: it.amount })),
    }));

    return NextResponse.json({ orders, count: orders.length });
  } catch (e: any) {
    // 502 so the Food tab shows an error with Retry instead of "No food orders found for this
    // period" while Ingest is down (iteration 203 E2 M11).
    return NextResponse.json({ error: e.message, orders: [], count: 0 }, { status: 502 });
  }
}
