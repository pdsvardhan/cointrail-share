"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * A page-wide data version (iteration 203 E3). A change that moves money between totals — a row
 * re-filed under another category, a payee rule saved or removed — bumps it, and every block on
 * the page that reads it fetches again in place (the old numbers stay on screen until the new ones
 * arrive), so all totals follow the change without a reload.
 */
const DataVersionContext = createContext<{ version: number; bump: () => void }>({
  version: 0,
  bump: () => {},
});

export function DataVersionProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo(() => ({ version, bump }), [version, bump]);
  return <DataVersionContext.Provider value={value}>{children}</DataVersionContext.Provider>;
}

export function useDataVersion() {
  return useContext(DataVersionContext);
}
