export default function CardsLoading() {
  return (
    <div className="min-h-screen space-y-6 p-6" style={{ background: "var(--background)" }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div
          className="h-8 w-40 animate-pulse rounded-lg"
          style={{ background: "var(--border)" }}
        />
        <div
          className="h-10 w-28 animate-pulse rounded-lg"
          style={{ background: "var(--border)" }}
        />
      </div>

      {/* Summary stat cards */}
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
              className="h-7 w-24 animate-pulse rounded"
              style={{ background: "var(--border)" }}
            />
          </div>
        ))}
      </div>

      {/* Cards grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-48 space-y-4 rounded-2xl p-6"
            style={{
              background: "linear-gradient(135deg, var(--border) 0%, var(--border-subtle) 100%)",
            }}
          >
            <div className="flex items-center justify-between">
              <div
                className="h-5 w-24 animate-pulse rounded"
                style={{ background: "rgba(255,255,255,0.15)" }}
              />
              <div
                className="h-6 w-6 animate-pulse rounded-full"
                style={{ background: "rgba(255,255,255,0.15)" }}
              />
            </div>
            <div
              className="mt-4 h-7 w-32 animate-pulse rounded"
              style={{ background: "rgba(255,255,255,0.15)" }}
            />
            <div className="mt-auto flex items-center justify-between">
              <div
                className="h-4 w-20 animate-pulse rounded"
                style={{ background: "rgba(255,255,255,0.1)" }}
              />
              <div
                className="h-4 w-16 animate-pulse rounded"
                style={{ background: "rgba(255,255,255,0.1)" }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
