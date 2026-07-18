import { LogOut } from 'lucide-react';

import { useLogout } from '@/features/auth/mutations';
import { useClaims } from '@/lib/use-claims';

import { Button } from '../ui/button';
import { ThemeToggle } from './theme-toggle';

export function UserFooter() {
  const claims = useClaims();
  const logout = useLogout();
  return (
    <div className="flex items-center justify-between border-t border-line-faint px-2 py-2">
      <div className="min-w-0">
        <p className="truncate text-[12px] font-medium text-ink">{claims?.sub ?? ''}</p>
        <p className="text-[11px] text-muted">{claims?.role ?? ''}</p>
      </div>
      <div className="flex items-center">
        <ThemeToggle />
        <Button variant="ghost" size="icon" aria-label="Sign out" onClick={() => logout.mutate()}>
          <LogOut className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
