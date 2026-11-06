import type { MetricSample, MetricValue } from '../api/types';
import { MEMORY_POOL_METRICS } from '../domain/memoryPools';
import { VM_IDS } from './data';

const CAPACITY = 8 * 1024;
const INTERVAL_MS = 2000;
const SAMPLES = 30;

const metric = (
  name: string,
  value: number,
  unit: string,
  labels: Record<string, string> | null = null,
): MetricValue => ({
  name,
  value,
  unit,
  labels,
  unavailable_reason: null,
  // v2 정수 원문·수집 상태 (Spring 조회 projection 과 같은 모양)
  raw_value: Number.isSafeInteger(value) ? String(value) : null,
  status: 'ok',
  source_rc: 0,
});

/** 업타임 기준점. 모든 mock 표본이 같은 부팅·Publisher 실행에서 나온 것처럼 업타임을 이어 준다 */
const uptimeOf = (observedAt: string, now: number) => Date.parse(observedAt) - (now - 3_600_000);

/**
 * 메모리 표본의 관측 문맥 (MetricSample.telemetry). Memory·Network 탭의 Pool·heap 계산은
 * 같은 관측 실행(boot_id·publisher_instance_id)에 속하고 원 관측 시각을 확인할 수 있는 표본만 현재값으로 쓴다.
 */
function memoryTelemetry(
  vmId: string,
  family: 'memory' | 'memory_regions',
  observedAt: string,
  seq: number,
  now: number,
): NonNullable<MetricSample['telemetry']> {
  const uptime = uptimeOf(observedAt, now);
  return {
    family,
    publisher_instance_id: `mock-publisher-${vmId}`,
    message_seq: String(seq),
    source_sample_seq: String(seq),
    observed_uptime_ms: String(uptime),
    sent_uptime_ms: String(uptime),
    sent_at: observedAt,
    utc_quality: 'synchronized',
    utc_source: 'mock',
    uncertainty_ms: null,
    collection: {
      sample_id: `${family}-${seq}`,
      start_uptime_ms: String(uptime),
      end_uptime_ms: String(uptime),
      elapsed_us: '100',
      status: 'ok',
      source_rc: 0,
    },
    context: {
      binding: 'mock',
      host_boot_id: 'mock-host-boot',
      execution_id: `mock-exec-${vmId}`,
      receipt_id: null,
    },
  };
}

function poolValues(region: 'net_rx_data' | 'net_tx_data', usedBytes: number): MetricValue[] {
  const labels = { region };
  return [
    metric(MEMORY_POOL_METRICS.used, usedBytes, 'bytes', labels),
    metric(MEMORY_POOL_METRICS.free, CAPACITY - usedBytes, 'bytes', labels),
    metric(MEMORY_POOL_METRICS.capacity, CAPACITY, 'bytes', labels),
  ];
}

const HEAP_INTERVAL_MS = 10_000;
const HEAP_SAMPLES = 30;
const HEAP_ACCOUNTED = 48 * 1024;

/**
 * Zephyr 커널 heap 표본(최근 5분, 10초 간격). 실제 Publisher 처럼 memory 계열만 담은 별도 표본이다.
 * VM A 는 사용량이 서서히 오르고, VM B 는 낮게 유지된다.
 */
export function mockKernelHeapSamples(vmId: string, now = Date.now()): MetricSample[] {
  const busy = vmId === VM_IDS.autonomous;
  let highWater = 0;
  return Array.from({ length: HEAP_SAMPLES }, (_, index) => {
    const used = busy ? 18 * 1024 + index * 512 : 9 * 1024 + (index % 5) * 256;
    highWater = Math.max(highWater, used + 1024);
    const observedAt = new Date(now - (HEAP_SAMPLES - 1 - index) * HEAP_INTERVAL_MS).toISOString();
    return {
      sample_id: `heap-${vmId}-${index}-${now}`,
      vm_id: vmId,
      boot_id: `mock-boot-${vmId}`,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: HEAP_INTERVAL_MS,
      telemetry: memoryTelemetry(vmId, 'memory', observedAt, 1_000 + index, now),
      values: [
        metric('memory.kernel_heap.used_bytes', used, 'bytes'),
        metric('memory.kernel_heap.free_bytes', HEAP_ACCOUNTED - used, 'bytes'),
        metric('memory.kernel_heap.high_water_bytes', highWater, 'bytes'),
        metric('memory.kernel_heap.accounted_bytes', HEAP_ACCOUNTED, 'bytes'),
      ],
    };
  });
}

/**
 * 정상 상태(VM B)와 RX 처리가 밀려 Pool 점유가 증가한 관측 예시(VM A).
 * 화면은 이 값을 실제 장애 판정이나 확정 임계값으로 사용하지 않는다.
 */
export function mockMemoryPoolSamples(vmId: string, now = Date.now()): MetricSample[] {
  const delayed = vmId === VM_IDS.autonomous;
  let rxPackets = 20_000;
  let txPackets = 15_000;

  return Array.from({ length: SAMPLES }, (_, index) => {
    const delayStep = Math.max(0, index - 14);
    const rxUsed = delayed ? Math.min(7 * 1024, 512 + delayStep * 512) : index % 6 === 0 ? 512 : 256;
    const txUsed = delayed ? (index % 7 === 0 ? 1280 : 1024) : index % 8 === 0 ? 768 : 512;
    const rxIncrement = delayed && delayStep > 0 ? 50 : delayed ? 170 : 130 + (index % 4) * 8;
    const txIncrement = delayed ? 96 + (index % 3) * 4 : 100 + (index % 5) * 5;
    rxPackets += rxIncrement;
    txPackets += txIncrement;

    const observedAt = new Date(now - (SAMPLES - 1 - index) * INTERVAL_MS).toISOString();
    const labels = { interface: 'netfront' };
    return {
      sample_id: `memory-${vmId}-${index}-${now}`,
      vm_id: vmId,
      boot_id: `mock-boot-${vmId}`,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: INTERVAL_MS,
      telemetry: memoryTelemetry(vmId, 'memory_regions', observedAt, 2_000 + index, now),
      values: [
        ...poolValues('net_rx_data', rxUsed),
        ...poolValues('net_tx_data', txUsed),
        metric(MEMORY_POOL_METRICS.rxPackets, rxPackets, 'packets', labels),
        metric(MEMORY_POOL_METRICS.txPackets, txPackets, 'packets', labels),
      ],
    };
  });
}

const NETWORK_INTERVAL_MS = 2000;
const NETWORK_SAMPLES = 30;

/**
 * Zephyr 네트워크 표본(최근 60초, 2초 간격).
 * - v1 바이트 카운터: value=null, counter_value(uint64 문자열) + 표본 network(publisher·업타임)
 * - v2 network 계열: rx_bytes_per_s 는 숫자, interface_up·carrier 는 지금 백엔드처럼
 *   boolean 을 읽지 못해 value=null·unavailable_reason="ok" 로 내려오는 모습을 그대로 흉내 낸다
 */
export function mockZephyrNetworkSamples(vmId: string, now = Date.now()): MetricSample[] {
  const busy = vmId === VM_IDS.autonomous;
  const bootId = `mock-boot-${vmId}`;
  const publisher = `mock-publisher-${vmId}`;
  const labels = { iface: 'eth0', counter_epoch: '1' };
  // 부팅 후 수십 MiB 를 주고받은 상태에서 시작한다 (2^53 초과 정밀도는 단위 테스트에서 검증)
  let rx = BigInt(busy ? 96 * 1024 * 1024 : 48 * 1024 * 1024);
  let tx = BigInt(busy ? 24 * 1024 * 1024 : 12 * 1024 * 1024);
  const samples: MetricSample[] = [];

  for (let index = 0; index < NETWORK_SAMPLES; index += 1) {
    const rxStep = BigInt(busy ? 24_000 + (index % 4) * 1_000 : 12_000 + (index % 3) * 500);
    const txStep = BigInt(busy ? 9_000 : 6_000 + (index % 2) * 400);
    rx += rxStep;
    tx += txStep;
    const observedAt = new Date(now - (NETWORK_SAMPLES - 1 - index) * NETWORK_INTERVAL_MS).toISOString();
    const uptime = String(600_000 + index * NETWORK_INTERVAL_MS);

    samples.push({
      sample_id: `net-v1-${vmId}-${index}-${now}`,
      vm_id: vmId,
      boot_id: bootId,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: null,
      network: { publisher_instance_id: publisher, observed_uptime_ms: uptime },
      values: [
        {
          name: 'net_rx_bytes_total',
          value: null,
          unit: 'bytes',
          labels,
          unavailable_reason: null,
          counter_value: rx.toString(),
          source_rc: 0,
        },
        {
          name: 'net_tx_bytes_total',
          value: null,
          unit: 'bytes',
          labels,
          unavailable_reason: null,
          counter_value: tx.toString(),
          source_rc: 0,
        },
      ],
    });
    samples.push({
      sample_id: `net-v2-${vmId}-${index}-${now}`,
      vm_id: vmId,
      boot_id: bootId,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: NETWORK_INTERVAL_MS,
      values: [
        metric('rx_bytes_per_s', Number(rxStep) / (NETWORK_INTERVAL_MS / 1000), 'bytes/s', {
          interface: 'eth0',
          counter_epoch: '1',
        }),
        {
          ...metric('network.interface_up', 0, 'bool', { interface: 'eth0' }),
          value: null,
          unavailable_reason: 'ok',
        },
        {
          ...metric('network.carrier', 0, 'bool', { interface: 'eth0' }),
          value: null,
          unavailable_reason: 'ok',
        },
      ],
    });
  }
  return samples;
}
