import { type LabelHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn('mb-1 block text-[12px] font-medium text-secondary', className)}
      {...props}
    />
  );
}
