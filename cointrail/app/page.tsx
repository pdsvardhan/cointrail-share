import { redirect } from "next/navigation";

export default function HomePage() {
  // Cointrail opens directly on the Credit Cards page.
  redirect("/cards");
}
