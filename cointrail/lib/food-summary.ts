/**
 * Expenses > Food: restaurant grouping (iteration 203 E2 M10).
 *
 * The search box and the Spend / Orders / Avg sort pills used to drive the headline cards too:
 * with sort = Avg, "Top Restaurant" became the highest-average place, and typing "pizza" turned
 * "Restaurants · unique places ordered from" into the match count. The headline is now computed
 * from every order, spend-sorted; search and sort only shape the grid.
 */

export interface FoodOrder {
  order_id?: string;
  platform?: string;
  restaurant_name?: string | null;
  total_amount?: number | null;
  order_date?: string | null;
  items?: { name: string; qty?: number; price?: number }[];
}

export interface RestaurantGroup {
  name: string;
  platform?: string;
  total: number;
  count: number;
  orders: FoodOrder[];
}

export type FoodSort = "total" | "count" | "avg";

function groupByRestaurant(orders: FoodOrder[]): RestaurantGroup[] {
  const by = new Map<string, RestaurantGroup>();
  for (const o of orders) {
    const name = o.restaurant_name || "Unknown";
    let g = by.get(name);
    if (!g) {
      g = { name, platform: o.platform, total: 0, count: 0, orders: [] };
      by.set(name, g);
    }
    g.total += o.total_amount || 0;
    g.count += 1;
    g.orders.push(o);
  }
  return [...by.values()];
}

const avg = (g: RestaurantGroup) => g.total / Math.max(1, g.count);

export function summarizeFood(orders: FoodOrder[], query: string, sort: FoodSort) {
  const all = groupByRestaurant(orders).sort((a, b) => b.total - a.total);
  const q = query.trim().toLowerCase();
  const grid = all
    .filter((r) => !q || r.name.toLowerCase().includes(q))
    .sort((a, b) =>
      sort === "count" ? b.count - a.count : sort === "avg" ? avg(b) - avg(a) : b.total - a.total
    );
  return {
    total: orders.reduce((s, o) => s + (o.total_amount || 0), 0),
    orderCount: orders.length,
    top: all[0] ?? null,
    restaurantCount: all.length,
    grid,
  };
}
