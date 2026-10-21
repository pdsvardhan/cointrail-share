import { proxyIngest } from "@/lib/ingest-route";

export const dynamic = "force-dynamic";

/** Every payee rule the owner made (iteration 203 E9), with how many payments each covers. */
export async function GET() {
  return proxyIngest("/payee-rules", undefined, { rules: [] });
}
