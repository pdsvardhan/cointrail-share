export default function ExpensesLoading() {
  return (
    <div className="min-h-screen space-y-6 p-6" style={{ background: "var(--background)" }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div
          className="h-8 w-48 animate-pulse rounded-lg"
          style={{ background: "var(--border)" }}
        />
        <div
          className="h-10 w-32 animate-pulse rounded-lg"
          style={{ background: "var(--border)" }}
        />
      </div>

      {/* Stat cards row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="space-y-3 rounded-xl p-5"
            style={{ background: "var(--card)", boxShadow: "var(--shadow-card)" }}
          >
            <div
              className="h-4 w-20 animate-pulse rounded"
              style={{ background: "var(--border)" }}
            />
            <div
              className="h-7 w-28 animate-pulse rounded"
              style={{ background: "var(--border)" }}
            />
            <div
              className="h-3 w-16 animate-pulse rounded"
              style={{ background: "var(--border-subtle)" }}
            />
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-9 w-24 animate-pulse rounded-lg"
            style={{ background: "var(--border)" }}
          />
        ))}
      </div>

      {/* Chart area */}
      <div
        className="rounded-xl p-6"
        style={{ background: "var(--card)", boxShadow: "var(--shadow-card)" }}
      >
        <div
          className="mb-4 h-5 w-36 animate-pulse rounded"
          style={{ background: "var(--border)" }}
        />
        <div
          className="h-64 w-full animate-pulse rounded-lg"
          style={{ background: "var(--border-subtle)" }}
        />
      </div>

      {/* Bottom grid */}
      <div className="grid gap-4 md:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div
            key={i}
            className="space-y-4 rounded-xl p-6"
            style={{ background: "var(--card)", boxShadow: "var(--shadow-card)" }}
          >
            <div
              className="h-5 w-32 animate-pulse rounded"
              style={{ background: "var(--border)" }}
            />
            {Array.from({ length: 4 }).map((_, j) => (
              <div key={j} className="flex items-center gap-3">
                <div
                  className="h-8 w-8 animate-pulse rounded-full"
                  style={{ background: "var(--border)" }}
                />
                <div
                  className="h-4 flex-1 animate-pulse rounded"
                  style={{ background: "var(--border-subtle)" }}
                />
                <div
                  className="h-4 w-16 animate-pulse rounded"
                  style={{ background: "var(--border)" }}
                />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
