import type { VcpuPinning } from '../api/types';

export function normalizePinning(mappings: VcpuPinning[]): VcpuPinning[] {
  return mappings
    .map(({ vcpu, pcpus }) => ({ vcpu, pcpus: [...new Set(pcpus)].sort((a, b) => a - b) }))
    .sort((a, b) => a.vcpu - b.vcpu);
}

export function pinningEqual(left: VcpuPinning[], right: VcpuPinning[]): boolean {
  return JSON.stringify(normalizePinning(left)) === JSON.stringify(normalizePinning(right));
}

export function togglePcpu(mappings: VcpuPinning[], vcpu: number, pcpu: number): VcpuPinning[] {
  return normalizePinning(
    mappings.map((mapping) => {
      if (mapping.vcpu !== vcpu) return mapping;
      const selected = mapping.pcpus.includes(pcpu);
      if (selected && mapping.pcpus.length === 1) return mapping;
      return {
        ...mapping,
        pcpus: selected ? mapping.pcpus.filter((id) => id !== pcpu) : [...mapping.pcpus, pcpu],
      };
    }),
  );
}

export function mappingLabel(mapping: VcpuPinning): string {
  return mapping.pcpus.map((pcpu) => `pCPU ${pcpu}`).join(', ');
}
