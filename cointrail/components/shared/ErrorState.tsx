"use client";

import { AlertCircle, RefreshCw } from "lucide-react";

/**
 * What a page shows when its data request failed (iteration 203 E2 M11): the message and a Retry
 * button, never an empty state that reads like "you have no data".
 */
export function ErrorState({
  message,
  onRetry,
  compact = false,
}: {
  message: string;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <div
      role="alert"
      data-role="error-state"
      className={`flex flex-col items-center justify-center gap-3 text-center ${compact ? "py-6" : "py-20"}`}
    >
      <AlertCircle
        className={`${compact ? "h-5 w-5" : "h-8 w-8"} text-red-700 dark:text-red-400`}
        aria-hidden
      />
      <p className="max-w-md text-sm text-[var(--foreground-secondary)]">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm font-medium text-emerald-700 transition-colors hover:bg-[var(--card-hover)] dark:text-emerald-400"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Retry
        </button>
      )}
    </div>
  );
}
