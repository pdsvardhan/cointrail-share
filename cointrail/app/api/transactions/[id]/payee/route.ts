import { badRequest, numericId, proxyIngest } from "@/lib/ingest-route";

export const dynamic = "force-dynamic";

/** What an "apply to all payments to this payee" rule would key on for this row (iteration 203 E3). */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const id = numericId((await params).id);
  if (!id) return badRequest("Invalid transaction id");
  return proxyIngest(`/transactions/${id}/payee`);
}
