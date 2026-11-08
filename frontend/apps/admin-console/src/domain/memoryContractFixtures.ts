import type { ManualBufferObservation, MetricSample, TelemetryExecutionContext } from '../api/types';
import fixtureSource from './fixtures/manualRam/cases.json?raw';

/** 시험 전용: 제공자 원문을 Spring의 검증된 조회 projection 모양으로 옮긴다. 제품 parser가 아니다. */
interface Wire {
  message_id: string;
  vm_id: string;
  boot_id: string;
  publisher_instance_id: string;
  seq: string;
  context: TelemetryExecutionContext;
  clock: {
    sent_uptime_ms: string;
    sent_at: string | null;
    utc_quality: string;
    utc_source: string | null;
    uncertainty_ms: number | null;
  };
  payload: {
    family: string;
    source_sample_seq?: string;
    observed_uptime_ms?: string;
    buffer?: ManualBufferObservation | null;
    collection: NonNullable<MetricSample['telemetry']>['collection'];
    samples: {
      name: string;
      value: string | number | boolean | null;
      unit: string;
      kind: string;
      encoding: string;
      labels: Record<string, string>;
      status: string;
      reason: string | null;
      source_rc: number;
    }[];
  };
}

const cases: Record<string, unknown> = JSON.parse(fixtureSource);
const files: Record<string, unknown> = Object.fromEntries(
  Object.entries(cases).map(([name, value]) => [`./fixtures/manualRam/${name}.json`, value]),
);
export const memoryWireNames = Object.keys(files).map((path) =>
  path
    .split('/')
    .at(-1)!
    .replace(/\.json$/, ''),
);
export function wireFixture(name: string): Wire {
  return structuredClone(files[`./fixtures/manualRam/${name}.json`]) as Wire;
}
export function memoryFixture(name: string, receivedAt = Date.parse('2026-11-04T10:00:00Z')): MetricSample {
  const message = wireFixture(name),
    p = message.payload,
    c = message.clock;
  return {
    vm_id: message.vm_id,
    boot_id: message.boot_id,
    sample_id: message.message_id,
    source: 'zephyr_telemetry',
    observed_at: c.sent_at ?? new Date(receivedAt).toISOString(),
    received_at: new Date(receivedAt).toISOString(),
    telemetry: {
      family: p.family,
      publisher_instance_id: message.publisher_instance_id,
      message_seq: message.seq,
      source_sample_seq: p.source_sample_seq ?? null,
      observed_uptime_ms: p.observed_uptime_ms ?? null,
      sent_uptime_ms: c.sent_uptime_ms,
      sent_at: c.sent_at,
      utc_quality: c.utc_quality,
      utc_source: c.utc_source,
      uncertainty_ms: c.uncertainty_ms,
      collection: p.collection,
      context: message.context,
      ...(Object.hasOwn(p, 'buffer') ? { buffer: p.buffer } : {}),
    },
    values: p.samples.map((value) => {
      const raw =
        value.status === 'ok' && value.encoding === 'u64_decimal' && typeof value.value === 'string'
          ? value.value
          : null;
      return {
        name: value.name,
        value:
          raw != null
            ? BigInt(raw) <= 9_007_199_254_740_991n
              ? Number(raw)
              : null
            : typeof value.value === 'number'
              ? value.value
              : null,
        raw_value: raw,
        counter_value: value.kind === 'raw_counter' ? raw : null,
        unit: value.unit,
        labels: value.labels,
        unavailable_reason: value.status === 'ok' ? null : (value.reason ?? value.status),
        status: value.status,
        source_rc: value.source_rc,
      };
    }),
  };
}
export const ramOracle = files['./fixtures/manualRam/oracle.json'] as {
  name: string;
  expected: {
    status: string;
    reason: string | null;
    total_bytes: string | null;
    committed_bytes: string | null;
    free_bytes: string | null;
    percent: string | null;
  };
}[];
