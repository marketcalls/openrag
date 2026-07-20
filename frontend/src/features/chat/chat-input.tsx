import { ArrowUp } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';

import type { ReasoningEffort } from '@/api/types';
import { NativeSelect } from '@/components/ui/select';

export function ChatInput({
  onSend,
  disabled,
  placeholder = 'Ask about your documents…',
  supportsReasoning = false,
  reasoningEffort = 'off',
  onReasoningEffortChange,
}: {
  onSend: (content: string) => void;
  disabled: boolean;
  placeholder?: string;
  supportsReasoning?: boolean;
  reasoningEffort?: ReasoningEffort;
  onReasoningEffortChange?: (effort: ReasoningEffort) => void;
}) {
  const [value, setValue] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue('');
    if (textarea.current) textarea.current.style.height = 'auto';
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="mx-auto w-full max-w-thread px-4 pb-4">
      <div className="rounded-xl border border-line bg-bg p-2 shadow-soft">
        <div className="flex items-end gap-2">
          <textarea
            ref={textarea}
            aria-label="Message"
            rows={1}
            value={value}
            disabled={disabled}
            placeholder={placeholder}
            onChange={(event) => {
              setValue(event.target.value);
              event.target.style.height = 'auto';
              event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
            }}
            onKeyDown={onKeyDown}
            className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1 text-[15px] text-ink outline-none placeholder:text-muted disabled:opacity-50"
          />
          <button
            type="button"
            aria-label="Send"
            disabled={disabled || value.trim() === ''}
            onClick={submit}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-ink text-bg disabled:opacity-40"
          >
            <ArrowUp className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {supportsReasoning && onReasoningEffortChange ? (
          <div className="mt-1 flex items-center border-t border-line px-1 pt-1.5">
            <NativeSelect
              aria-label="Reasoning effort"
              value={reasoningEffort}
              disabled={disabled}
              onChange={(event) =>
                onReasoningEffortChange(event.target.value as ReasoningEffort)
              }
              className="h-6 w-auto border-0 bg-transparent px-1 text-[11px] text-secondary"
            >
              <option value="off">Reasoning: off</option>
              <option value="low">Reasoning: low</option>
              <option value="medium">Reasoning: medium</option>
              <option value="high">Reasoning: high</option>
            </NativeSelect>
            <span className="ml-2 text-[10px] text-muted">No reasoning trace is shown</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
