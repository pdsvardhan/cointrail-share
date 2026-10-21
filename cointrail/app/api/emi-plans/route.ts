import { proxyIngest } from "@/lib/ingest-route";
import { pickQuery } from "@/lib/query";

export const dynamic = "force-dynamic";

/** EMI plans, one item per converted purchase (iteration 203 E6); `card_last4` filters. */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const query = pickQuery(searchParams, ["card_last4", "include_completed"]);
  return proxyIngest(`/emi-plans${query ? `?${query}` : ""}`, undefined, { plans: [] });
}
