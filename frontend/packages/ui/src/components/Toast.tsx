import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Health } from './StatusBadge';
import styles from './Toast.module.css';

interface ToastItem {
  id: number;
  tone: Health | 'info';
  text: ReactNode;
}

interface ToastApi {
  show: (text: ReactNode, tone?: ToastItem['tone'], durationMs?: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const show = useCallback<ToastApi['show']>((text, tone = 'info', durationMs = 3000) => {
    const id = nextId.current++;
    setItems((current) => [...current, { id, tone, text }]);
    window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), durationMs);
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.stack} aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={[styles.toast, styles[item.tone]].join(' ')}>
            {item.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error('useToast 는 <ToastProvider> 안에서만 쓴다');
  return api;
}
