import type { MetricSample } from '../api/types';

/** 유한 조회 이력 안의 최근 메모리 관측 세션을 고른다. 실제 현재 실행의 증명은 아니다. */
export function memoryObservationRunKey(sample: MetricSample): string | null {
  const meta = sample.telemetry;
  return sample.boot_id && meta?.publisher_instance_id && meta.context
    ? JSON.stringify([sample.vm_id, sample.boot_id, meta.publisher_instance_id])
    : null;
}

export function selectMemoryObservationRun(samples: readonly MetricSample[], vmId: string) {
  const reports = samples
    .filter(
      (sample) =>
        sample.vm_id === vmId &&
        (['memory', 'memory_regions', 'app_memory'].includes(sample.telemetry?.family ?? '') ||
          sample.values.some((value) => value.name.startsWith('memory.'))) &&
        Number.isFinite(Date.parse(sample.received_at ?? '')),
    )
    .sort((a, b) => Date.parse(a.received_at!) - Date.parse(b.received_at!));
  const retired = new Set<string>();
  const retiredBoots = new Set<string>();
  const boots = new Map(reports.map((sample) => [memoryObservationRunKey(sample), sample.boot_id]));
  const isRetired = (key: string) => retired.has(key) || retiredBoots.has(boots.get(key)!);
  const retire = (key: string, successor?: string) => {
    retired.add(key);
    if (successor && boots.get(key) !== boots.get(successor)) retiredBoots.add(boots.get(key)!);
  };
  let candidates = new Set<string>();
  let key: string | null = null;
  let state: 'selected' | 'missing' | 'ambiguous' | 'unverified' = 'missing';
  for (let index = 0; index < reports.length;) {
    const at = Date.parse(reports[index]!.received_at!);
    const group: MetricSample[] = [];
    while (index < reports.length && Date.parse(reports[index]!.received_at!) === at)
      group.push(reports[index++]!);
    const keys = new Set(
      group
        .map(memoryObservationRunKey)
        .filter((value): value is string => value != null && !isRetired(value)),
    );
    if (keys.size === 0) {
      if (group.some((sample) => memoryObservationRunKey(sample) == null)) state = 'unverified';
      continue;
    }
    if (keys.size === 1) {
      const next = [...keys][0]!;
      for (const previous of candidates) if (previous !== next) retire(previous, next);
      if (key && key !== next) retire(key, next);
      key = next;
      candidates = new Set([next]);
      state = 'selected';
    } else {
      // 같은 수신 시각의 서로 다른 새 세션은 배열 순서나 서로 다른 uptime으로 정렬하지 않는다.
      if (key)
        retire(
          key,
          [...keys].find((value) => boots.get(value) !== boots.get(key)),
        );
      candidates = new Set([...candidates, ...keys].filter((value) => !isRetired(value)));
      key = null;
      state = 'ambiguous';
    }
    if (group.some((sample) => memoryObservationRunKey(sample) == null)) state = 'unverified';
  }
  return { key, state };
}
