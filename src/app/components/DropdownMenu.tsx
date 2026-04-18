import { useEffect, useMemo, useRef, useState } from 'react';
 
export type DropdownMenuItem =
  | { kind: 'item'; label: string; onClick: () => void; disabled?: boolean; danger?: boolean }
  | { kind: 'divider' };
 
export function DropdownMenu({
  trigger,
  items,
}: {
  trigger: (args: { open: boolean; setOpen: (v: boolean) => void }) => React.ReactNode;
  items: DropdownMenuItem[];
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
 
  const normalized = useMemo(() => {
    return (items || []).filter(Boolean);
  }, [items]);
 
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const el = rootRef.current;
      if (!el) return;
      if (e.target instanceof Node && el.contains(e.target)) return;
      setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [open]);
 
  return (
    <div ref={rootRef} className="relative inline-block">
      {trigger({ open, setOpen })}
      {open && (
        <div className="absolute right-0 mt-2 w-56 rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden z-50">
          {normalized.map((it, idx) => {
            if (it.kind === 'divider') {
              return <div key={`d-${idx}`} className="h-px bg-gray-100" />;
            }
            const disabled = it.disabled === true;
            const tone = it.danger ? 'text-red-600' : 'text-gray-700';
            return (
              <button
                key={`i-${idx}`}
                type="button"
                disabled={disabled}
                onClick={() => {
                  if (disabled) return;
                  setOpen(false);
                  it.onClick();
                }}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50 ${tone}`}
              >
                {it.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

