import { badRequest, numericId, proxyIngest } from "@/lib/ingest-route";

/** Undo a cancellation: the series is active again (iteration 203 E5). */
export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const id = numericId((await params).id);
  if (!id) return badRequest("Invalid cancellation id");
  return proxyIngest(`/recurring/cancellations/${id}`, { method: "DELETE" });
}
