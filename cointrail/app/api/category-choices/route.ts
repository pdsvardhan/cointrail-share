import { proxyIngest } from "@/lib/ingest-route";

export const dynamic = "force-dynamic";

/** Categories a transaction can be filed under, each with the group it counts in (iteration 203 E3). */
export async function GET() {
  return proxyIngest("/category-choices", undefined, { categories: [] });
}
