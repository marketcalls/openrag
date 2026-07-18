import { createContext, useContext, type ReactNode } from 'react';

const CitationContext = createContext<(marker: number) => void>(() => undefined);

export function CitationProvider({
  onCitationClick,
  children,
}: {
  onCitationClick: (marker: number) => void;
  children: ReactNode;
}) {
  return <CitationContext.Provider value={onCitationClick}>{children}</CitationContext.Provider>;
}

export function useCitationClick(): (marker: number) => void {
  return useContext(CitationContext);
}
