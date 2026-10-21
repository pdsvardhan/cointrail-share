export default function CardDetailLoading() {
  return (
    <div className="min-h-screen space-y-6 p-6" style={{ background: "var(--background)" }}>
      {/* Back button + title */}
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 animate-pulse rounded-lg" style={{ background: "var(--border)" }} />
        <div
          className="h-7 w-48 animate-pulse rounded-lg"
          style={{ background: "var(--border)" }}
        />
      </div>

      {/* Card hero */}
      <div
        className="h-40 rounded-2xl p-6"
        style={{
          background: "linear-gradient(135deg, var(--border) 0%, var(--border-subtle) 100%)",
        }}
      >
        <div
          className="mb-3 h-5 w-32 animate-pulse rounded"
          style={{ background: "rgba(255,255,255,0.15)" }}
        />
        <div
          className="mb-4 h-8 w-40 animate-pulse rounded"
          style={{ background: "rgba(255,255,255,0.15)" }}
        />
        <div className="flex gap-6">
          <div
            className="h-4 w-24 animate-pulse rounded"
            style={{ background: "rgba(255,255,255,0.1)" }}
          />
          <div
            className="h-4 w-24 animate-pulse rounded"
            style={{ background: "rgba(255,255,255,0.1)" }}
          />
        </div>
      </div>

      {/* Stat cards */}
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

      {/* Transactions list */}
      <div
        className="space-y-4 rounded-xl p-6"
        style={{ background: "var(--card)", boxShadow: "var(--shadow-card)" }}
      >
        <div className="h-5 w-36 animate-pulse rounded" style={{ background: "var(--border)" }} />
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 py-2">
            <div
              className="h-10 w-10 animate-pulse rounded-full"
              style={{ background: "var(--border)" }}
            />
            <div className="flex-1 space-y-2">
              <div
                className="h-4 w-40 animate-pulse rounded"
                style={{ background: "var(--border)" }}
              />
              <div
                className="h-3 w-24 animate-pulse rounded"
                style={{ background: "var(--border-subtle)" }}
              />
            </div>
            <div
              className="h-5 w-20 animate-pulse rounded"
              style={{ background: "var(--border)" }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
