import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { useVmAction, type VmAction } from '../api/queries';
import type { Vm } from '../api/types';

export type VmOperation = {
  action: VmAction;
  startedAt: number;
  status: 'pending' | 'failed';
  error?: string;
};

type OperationCallbacks = {
  onSuccess?: () => void;
  onError?: (error: unknown, message: string) => void;
};

export function useVmOperationProgress() {
  const mutation = useVmAction();
  const pendingVmIds = useRef(new Set<string>());
  const [operations, setOperations] = useState<Record<string, VmOperation>>({});
  const [now, setNow] = useState(() => Date.now());
  const hasPendingOperation = Object.values(operations).some((operation) => operation.status === 'pending');

  useEffect(() => {
    if (!hasPendingOperation) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasPendingOperation]);

  const run = (vm: Vm, action: VmAction, callbacks: OperationCallbacks = {}) => {
    if (pendingVmIds.current.has(vm.vm_id)) return;

    pendingVmIds.current.add(vm.vm_id);
    const startedAt = Date.now();
    setNow(startedAt);
    setOperations((current) => ({
      ...current,
      [vm.vm_id]: { action, startedAt, status: 'pending' },
    }));

    void mutation
      .mutateAsync({ vmId: vm.vm_id, action })
      .then(() => {
        pendingVmIds.current.delete(vm.vm_id);
        setOperations((current) => withoutOperation(current, vm.vm_id));
        callbacks.onSuccess?.();
      })
      .catch((error: unknown) => {
        pendingVmIds.current.delete(vm.vm_id);
        const message = vmOperationErrorMessage(error);
        setOperations((current) => ({
          ...current,
          [vm.vm_id]: { action, startedAt, status: 'failed', error: message },
        }));
        callbacks.onError?.(error, message);
      });
  };

  return { operations, now, run };
}

export function formatElapsedTime(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function vmOperationErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

function withoutOperation(
  operations: Record<string, VmOperation>,
  vmId: string,
): Record<string, VmOperation> {
  const next = { ...operations };
  delete next[vmId];
  return next;
}
