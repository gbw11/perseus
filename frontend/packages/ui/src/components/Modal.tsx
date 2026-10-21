import { useEffect, useRef, type ReactNode } from 'react';
import styles from './Modal.module.css';

export interface ModalProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
}

/** 네이티브 <dialog> 기반. Esc·바깥 클릭으로 닫힌다. */
export function Modal({ open, title, onClose, footer, children }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal?.();
    if (!open && dialog.open) dialog.close?.();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
      aria-label={typeof title === 'string' ? title : undefined}
    >
      <header className={styles.header}>
        <h2 className={styles.title}>{title}</h2>
        <button type="button" className={styles.close} onClick={onClose} aria-label="닫기">
          ×
        </button>
      </header>
      <div className={styles.body}>{children}</div>
      {footer && <footer className={styles.footer}>{footer}</footer>}
    </dialog>
  );
}
