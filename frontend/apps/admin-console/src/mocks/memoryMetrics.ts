import type { ManualBufferObservation, MetricSample, MetricValue } from '../api/types';
import { MEMORY_POOL_METRICS } from '../domain/memoryPools';
import { VM_IDS } from './data';

const CAPACITY = 8 * 1024;
const INTERVAL_MS = 2000;

/**
 * 메모리 표본(heap·Pool·앱 버퍼)의 가장 최근 관측 시각을 조회 시각보다 이만큼 과거로 둔다.
 * 화면의 1초 시계(useNow)는 최대 1초 늦을 수 있어, 조회 시각 그대로면 최근 표본이 "미래 관측"으로 걸러진다.
 * 실제 데이터처럼 조금 전에 관측된 표본이 되게 하며, 만료 기준(6초) 안에 든다.
 */
const OBSERVATION_DELAY_MS = 2000;
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
    const observedAt = new Date(
      now - OBSERVATION_DELAY_MS - (HEAP_SAMPLES - 1 - index) * HEAP_INTERVAL_MS,
    ).toISOString();
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

    const observedAt = new Date(
      now - OBSERVATION_DELAY_MS - (SAMPLES - 1 - index) * INTERVAL_MS,
    ).toISOString();
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

type AppRegion =
  | 'drive_maps'
  | 'drive_paths'
  | 'drive_search_nodes'
  | 'drive_odometry'
  | 'manual_input'
  | 'manual_pending'
  | 'manual_running'
  | 'manual_result';

/** 영역 한 개의 값 (KiB). used ≤ peak ≤ capacity ≤ reserved, free = capacity − used, min_free = capacity − peak */
interface AppRegionMock {
  reservedKiB: number;
  capacityKiB: number;
  usedKiB: number;
  peakKiB: number;
  capacityFailures: number;
  contention: number;
}

const KIB = 1024;

/**
 * 앱 버퍼 영역 mock 값. VM A 는 지도 풀 87.5%(주의 색)·경로 풀 공간 부족 1회,
 * VM B 는 조작 입력이 만료 상태(이상 입력 신호). 수동 대기·실행·결과 슬롯은 비어 있다.
 */
const APP_REGION_MOCK: Record<string, Record<AppRegion, AppRegionMock>> = {
  [VM_IDS.autonomous]: {
    drive_maps: {
      reservedKiB: 36,
      capacityKiB: 32,
      usedKiB: 28,
      peakKiB: 30,
      capacityFailures: 0,
      contention: 2,
    },
    drive_paths: {
      reservedKiB: 18,
      capacityKiB: 16,
      usedKiB: 6,
      peakKiB: 16,
      capacityFailures: 1,
      contention: 0,
    },
    drive_search_nodes: {
      reservedKiB: 64,
      capacityKiB: 48,
      usedKiB: 12,
      peakKiB: 40,
      capacityFailures: 0,
      contention: 0,
    },
    drive_odometry: {
      reservedKiB: 9,
      capacityKiB: 8,
      usedKiB: 5,
      peakKiB: 8,
      capacityFailures: 0,
      contention: 0,
    },
    manual_input: {
      reservedKiB: 4.5,
      capacityKiB: 4,
      usedKiB: 1.5,
      peakKiB: 3,
      capacityFailures: 0,
      contention: 0,
    },
    manual_pending: {
      reservedKiB: 1,
      capacityKiB: 1,
      usedKiB: 0,
      peakKiB: 1,
      capacityFailures: 0,
      contention: 0,
    },
    manual_running: {
      reservedKiB: 1.25,
      capacityKiB: 1.25,
      usedKiB: 0,
      peakKiB: 1.25,
      capacityFailures: 0,
      contention: 0,
    },
    manual_result: {
      reservedKiB: 1.5,
      capacityKiB: 1.5,
      usedKiB: 0,
      peakKiB: 1.5,
      capacityFailures: 0,
      contention: 0,
    },
  },
  [VM_IDS.manual]: {
    drive_maps: {
      reservedKiB: 36,
      capacityKiB: 32,
      usedKiB: 12,
      peakKiB: 20,
      capacityFailures: 0,
      contention: 0,
    },
    drive_paths: {
      reservedKiB: 18,
      capacityKiB: 16,
      usedKiB: 3,
      peakKiB: 8,
      capacityFailures: 0,
      contention: 0,
    },
    drive_search_nodes: {
      reservedKiB: 64,
      capacityKiB: 48,
      usedKiB: 30,
      peakKiB: 36,
      capacityFailures: 0,
      contention: 0,
    },
    drive_odometry: {
      reservedKiB: 9,
      capacityKiB: 8,
      usedKiB: 4,
      peakKiB: 6,
      capacityFailures: 0,
      contention: 0,
    },
    manual_input: {
      reservedKiB: 4.5,
      capacityKiB: 4,
      usedKiB: 2,
      peakKiB: 3.5,
      capacityFailures: 0,
      contention: 1,
    },
    manual_pending: {
      reservedKiB: 1,
      capacityKiB: 1,
      usedKiB: 0,
      peakKiB: 1,
      capacityFailures: 0,
      contention: 0,
    },
    manual_running: {
      reservedKiB: 1.25,
      capacityKiB: 1.25,
      usedKiB: 0,
      peakKiB: 1.25,
      capacityFailures: 0,
      contention: 0,
    },
    manual_result: {
      reservedKiB: 1.5,
      capacityKiB: 1.5,
      usedKiB: 0,
      peakKiB: 1.5,
      capacityFailures: 0,
      contention: 0,
    },
  },
};

/** memory.app.* 여덟 지표. 팀원 검증(appMemory.ts)대로 u64 원문(raw_value)·단위·status 를 채운다 */
function appRegionValues(region: AppRegion, mock: AppRegionMock): MetricValue[] {
  const bytes = (kib: number) => Math.round(kib * KIB);
  const capacity = bytes(mock.capacityKiB);
  const used = bytes(mock.usedKiB);
  const peak = bytes(mock.peakKiB);
  const fields: [string, number, string][] = [
    ['reserved_bytes', bytes(mock.reservedKiB), 'bytes'],
    ['used_bytes', used, 'bytes'],
    ['free_bytes', capacity - used, 'bytes'],
    ['capacity_bytes', capacity, 'bytes'],
    ['high_water_bytes', peak, 'bytes'],
    ['min_free_bytes', capacity - peak, 'bytes'],
    ['capacity_failure_total', mock.capacityFailures, 'count'],
    ['contention_total', mock.contention, 'count'],
  ];
  return fields.map(([name, value, unit]) => metric(`memory.app.${name}`, value, unit, { region }));
}

const MANUAL_REGIONS = ['manual_input', 'manual_pending', 'manual_running', 'manual_result'] as const;

/** 수동 영역 버퍼 문맥. 조작 입력만 보관(stored) 상태, 나머지 슬롯은 비어 있다(empty) */
function manualBuffer(
  region: (typeof MANUAL_REGIONS)[number],
  mock: AppRegionMock,
  inputStatus: ManualBufferObservation['input_status'],
  uptime: number,
): ManualBufferObservation {
  const stored = region === 'manual_input';
  return {
    phase: stored ? 'stored' : 'empty',
    content_bytes: stored ? String(Math.round(mock.usedKiB * KIB)) : '0',
    sequence: stored ? '41' : null,
    source_uptime_ms: stored ? String(Math.max(0, uptime - 1500)) : null,
    owner_lifetime: '1',
    context_revision: '3',
    connection_revision: '2',
    input_status: stored ? inputStatus : 'missing',
    source_rc: 0,
    performed: region === 'manual_result' ? false : null,
    revoked: false,
    last_read_sequence: null,
  };
}

/**
 * Zephyr 앱 버퍼 영역 표본 (family app_memory, 최근 60초, 2초 간격).
 * 자동·공통 네 영역은 한 표본에, 수동 영역은 버퍼 문맥이 영역마다 달라 영역별 표본으로 보낸다.
 * 관측 문맥은 heap·Pool mock 과 같은 관측 실행(boot_id·publisher)이라 Memory 탭에서 현재값으로 쓰인다.
 */
export function mockAppMemorySamples(vmId: string, now = Date.now()): MetricSample[] {
  const regions = APP_REGION_MOCK[vmId];
  if (!regions) return [];
  const inputStatus = vmId === VM_IDS.manual ? 'expired' : 'accepted';
  return Array.from({ length: SAMPLES }, (_, index) => {
    const observedAt = new Date(
      now - OBSERVATION_DELAY_MS - (SAMPLES - 1 - index) * INTERVAL_MS,
    ).toISOString();
    const seq = 3_000 + index;
    const base = {
      vm_id: vmId,
      boot_id: `mock-boot-${vmId}`,
      source: 'zephyr_telemetry',
      observed_at: observedAt,
      received_at: observedAt,
      window_ms: INTERVAL_MS,
    };
    const telemetry = memoryTelemetry(vmId, 'memory_regions', observedAt, seq, now);
    const uptime = uptimeOf(observedAt, now);
    const automatic: MetricSample = {
      ...base,
      sample_id: `app-${vmId}-${index}-${now}`,
      telemetry: { ...telemetry, family: 'app_memory' },
      values: (['drive_maps', 'drive_paths', 'drive_search_nodes', 'drive_odometry'] as const).flatMap(
        (region) => appRegionValues(region, regions[region]),
      ),
    };
    const manual = MANUAL_REGIONS.map((region): MetricSample => ({
      ...base,
      sample_id: `app-${vmId}-${region}-${index}-${now}`,
      telemetry: {
        ...telemetry,
        family: 'app_memory',
        buffer: manualBuffer(region, regions[region], inputStatus, uptime),
      },
      values: appRegionValues(region, regions[region]),
    }));
    return [automatic, ...manual];
  }).flat();
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
