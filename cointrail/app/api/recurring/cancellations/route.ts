import { badRequest, jsonBody, proxyIngest } from "@/lib/ingest-route";
import { isValidDay } from "@/lib/recurring";

/**
 * Mark a recurring series cancelled (iteration 203 E5). Body: {series_key, cancelled_on?,
 * display_name?}. Ingest takes the series out of the active list and its totals, and warns if it
 * charges again after `cancelled_on`.
 */
export async function POST(request: Request) {
  const body = await jsonBody(request);
  const seriesKey = typeof body?.series_key === "string" ? body.series_key.trim() : "";
  if (!seriesKey) return badRequest("series_key is required");
  const forward: Record<string, unknown> = { series_key: seriesKey };
  if (body?.cancelled_on !== undefined) {
    if (typeof body.cancelled_on !== "string" || !isValidDay(body.cancelled_on)) {
      return badRequest("cancelled_on must be a YYYY-MM-DD date");
    }
    forward.cancelled_on = body.cancelled_on;
  }
  if (typeof body?.display_name === "string" && body.display_name.trim()) {
    forward.display_name = body.display_name.trim();
  }
  return proxyIngest("/recurring/cancellations", { method: "POST", body: JSON.stringify(forward) });
}
