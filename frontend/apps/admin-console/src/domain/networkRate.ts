import type { MetricSample, MetricValue, Vm } from '../api/types';

const RX_TOTAL = 'net_rx_bytes_total';
const RX_RATE = 'rx_bytes_per_s';
const UINT64_MAX = 18_446_744_073_709_551_615n;

/** uint64 십진 문자열만 받는다 (실제 0 은 "0"). Network 탭 counter_value 차분도 함께 쓴다 */
export function unsigned(value: string | null | undefined): bigint | null {
  if (!value || !/^(0|[1-9][0-9]{0,19})$/.test(value)) return null;
  const parsed = BigInt(value);
  return parsed <= UINT64_MAX ? parsed : null;
}

interface Counter {
  bytes: bigint;
  timeMs: bigint;
  identity: string;
}

function counter(sample: MetricSample, value: MetricValue, zephyr: boolean): Counter | null {
  const iface = value.labels?.iface;
  if (!sample.boot_id || !iface || value.unavailable_reason || value.unit !== 'bytes') return null;
  if (zephyr) {
    const publisher = sample.network?.publisher_instance_id;
    const epoch = unsigned(value.labels?.counter_epoch);
    const bytes = unsigned(value.counter_value);
    const timeMs = unsigned(sample.network?.observed_uptime_ms);
    if (!publisher || epoch == null || epoch === 0n || bytes == null || timeMs == null) return null;
    return {
      bytes,
      timeMs,
      identity: JSON.stringify([sample.vm_id, sample.boot_id, iface, publisher, epoch.toString()]),
    };
  }
  const timeMs = Date.parse(sample.observed_at);
  if (
    value.value == null ||
    !Number.isSafeInteger(value.value) ||
    value.value < 0 ||
    !Number.isFinite(timeMs)
  )
    return null;
  return {
    bytes: BigInt(value.value),
    timeMs: BigInt(timeMs),
    identity: JSON.stringify([sample.vm_id, sample.boot_id, iface]),
  };
}

/** Zephyr's measured gauge wins when present. Counter fallback never crosses an observation boundary. */
export function networkRxKiBPerSecond(
  samples: readonly MetricSample[],
  kind: Vm['guest_kind'],
): (number | null)[] {
  if (kind === 'zephyr') {
    const direct = samples.flatMap((sample) => sample.values.filter((value) => value.name === RX_RATE));
    if (direct.length > 0) {
      return direct.map(({ value, unit, unavailable_reason }) =>
        unit === 'bytes/s' && !unavailable_reason && value != null && Number.isFinite(value) && value >= 0
          ? value / 1024
          : null,
      );
    }
  }
  const reports = samples.flatMap((sample) => {
    const values = sample.values.filter((value) => value.name === RX_TOTAL);
    return values.length ? [{ sample, values }] : [];
  });
  const interfaces =
    reports.at(-1)?.values.flatMap(({ labels }) => (labels?.iface ? [labels.iface] : [])) ?? [];
  const iface = interfaces.includes('eth0') ? 'eth0' : interfaces[0];
  let previous: Counter | null = null;
  return reports.flatMap(({ sample, values }) => {
    const value = values.find((entry) => entry.labels?.iface === iface);
    const current = value ? counter(sample, value, kind === 'zephyr') : null;
    const before = previous;
    if (before && current && current.identity === before.identity) {
      // The publisher can send its cached observation more than once.
      if (
        current.timeMs < before.timeMs ||
        (current.timeMs === before.timeMs && current.bytes === before.bytes)
      )
        return [];
    }
    previous = current;
    if (
      !before ||
      !current ||
      current.identity !== before.identity ||
      current.timeMs === before.timeMs ||
      current.bytes < before.bytes
    )
      return [null];
    return [(Number(current.bytes - before.bytes) * 1000) / Number(current.timeMs - before.timeMs) / 1024];
  });
}
