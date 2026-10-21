import RecurringExpenses from "@/components/expenses/RecurringExpenses";

export default function RecurringExpensesPage() {
  return (
    <div className="min-h-screen bg-[var(--background)] px-4 py-8 md:px-8">
      <div className="mx-auto max-w-4xl">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-[var(--foreground)]">Recurring Expenses</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Auto-detected subscriptions, EMIs, and recurring payments from your transaction history
          </p>
        </div>
        <RecurringExpenses />
      </div>
    </div>
  );
}
