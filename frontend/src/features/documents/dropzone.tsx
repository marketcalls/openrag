import { Upload } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';

import { cn } from '@/lib/cn';

const ACCEPTED_FILES = '.pdf,.docx,.xlsx,.pptx,.csv,.txt,.md';

export function Dropzone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onFiles(files);
  };

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        aria-label="Upload documents"
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          'flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed border-line-strong bg-raised px-4 py-8 text-secondary hover:border-accent',
          dragOver && 'border-accent bg-accent-soft',
          disabled && 'opacity-50',
        )}
      >
        <Upload className="h-5 w-5 text-muted" aria-hidden />
        <span className="text-[13px] font-medium text-ink">
          Drop files here or click to upload
        </span>
        <span className="text-[12px] text-muted">
          PDF, DOCX, XLSX, PPTX, CSV, TXT, MD
        </span>
      </button>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPTED_FILES}
        className="hidden"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) onFiles(files);
          event.target.value = '';
        }}
      />
    </>
  );
}
