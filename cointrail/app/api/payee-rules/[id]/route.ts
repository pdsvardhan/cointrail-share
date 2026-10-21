import { badRequest, numericId, proxyIngest } from "@/lib/ingest-route";

/**
 * Remove a payee rule (iteration 203 E9). Ingest gives the payments it labelled their own
 * categoriser label back; payments the owner re-filed by hand keep theirs.
 */
export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const id = numericId((await params).id);
  if (!id) return badRequest("Invalid rule id");
  return proxyIngest(`/payee-rules/${id}`, { method: "DELETE" });
}
