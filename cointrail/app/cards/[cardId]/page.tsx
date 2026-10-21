import CardDetail from "@/components/cards/CardDetail";

export default async function CardDetailPage({ params }: { params: Promise<{ cardId: string }> }) {
  const { cardId } = await params;
  return <CardDetail cardId={cardId} />;
}
