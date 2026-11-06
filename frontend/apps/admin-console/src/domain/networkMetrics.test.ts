import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import {
  BACKEND_UNSUPPORTED,
  buildLinuxNetworkSeries,
  buildRxByteRateSeries,
  buildZephyrByteSeries,
  displayReason,
  interfaceFlagOf,
  mergeRxRateFallback,
  withRateFallback,
  ZEPHYR_INTERFACE_UP_METRIC,
} from './networkMetrics';

const metric = (value: Partial<MetricValue> & { name: string }): MetricValue => ({
  value: null,
  unit: 'bytes',
  labels: null,
  unavailable_reason: null,
  ...value,
});

const sample = (
  observedAt: string,
  values: MetricValue[],
  extra: Partial<MetricSample> = {},
): MetricSample => ({
  sample_id: crypto.randomUUID(),
  vm_id: 'vm',
  boot_id: 'boot-1',
  source: 'guest_agent',
  observed_at: observedAt,
  values,
  ...extra,
});

describe('Linux 송수신', () => {
  const linux = (at: string, rx: [string, number][], tx: [string, number][], bootId = 'boot-1') =>
    sample(
      at,
      [
        ...rx.map(([iface, value]) => metric({ name: 'net_rx_bytes_total', value, labels: { iface } })),
        ...tx.map(([iface, value]) => metric({ name: 'net_tx_bytes_total', value, labels: { iface } })),
      ],
      { boot_id: bootId },
    );

  it('인터페이스별 값을 합산하고 같은 부팅·인터페이스 묶음이면 속도를 구한다', () => {
    const series = buildLinuxNetworkSeries([
      linux(
        '2026-11-04T01:00:00Z',
        [
          ['eth0', 1000],
          ['eth1', 0],
        ],
        [
          ['eth0', 500],
          ['eth1', 0],
        ],
      ),
      linux(
        '2026-11-04T01:00:10Z',
        [
          ['eth1', 1000],
          ['eth0', 2000],
        ],
        [
          ['eth0', 1500],
          ['eth1', 0],
        ],
      ),
    ]);
    expect(series.rx).toEqual({ totalBytes: 3000, bytesPerSecond: 200, reason: null });
    expect(series.tx).toEqual({ totalBytes: 1500, bytesPerSecond: 100, reason: null });
    expect(series.rates).toEqual([{ observedAt: '2026-11-04T01:00:10Z', rx: 200, tx: 100 }]);
  });

  it('재부팅·카운터 감소·인터페이스 변경이면 음수나 엉뚱한 속도를 만들지 않는다', () => {
    const series = buildLinuxNetworkSeries([
      linux('2026-11-04T01:00:00Z', [['eth0', 9000]], [['eth0', 9000]]),
      linux('2026-11-04T01:00:10Z', [['eth0', 100]], [['eth0', 100]], 'boot-2'),
      linux('2026-11-04T01:00:20Z', [['eth0', 50]], [['eth0', 200]], 'boot-2'),
      linux(
        '2026-11-04T01:00:30Z',
        [
          ['eth0', 60],
          ['eth1', 5],
        ],
        [
          ['eth0', 300],
          ['eth1', 0],
        ],
        'boot-2',
      ),
    ]);
    expect(series.rates.map((point) => [point.rx, point.tx])).toEqual([
      [null, null],
      [null, 10],
      [null, null],
    ]);
  });
});

describe('Zephyr RX/TX 바이트 (counter_value)', () => {
  const zephyr = (
    at: string,
    rx: string | null,
    tx: string | null,
    {
      uptime = '1000',
      bootId = 'boot-1',
      publisher = 'pub-1',
      epoch = '1',
      reason = null as string | null,
    } = {},
  ) =>
    sample(
      at,
      [
        metric({
          name: 'net_rx_bytes_total',
          counter_value: rx,
          labels: { iface: 'eth0', counter_epoch: epoch },
          unavailable_reason: rx == null ? reason : null,
        }),
        metric({
          name: 'net_tx_bytes_total',
          counter_value: tx,
          labels: { iface: 'eth0', counter_epoch: epoch },
          unavailable_reason: tx == null ? reason : null,
        }),
      ],
      {
        source: 'zephyr_telemetry',
        boot_id: bootId,
        network: { publisher_instance_id: publisher, observed_uptime_ms: uptime },
      },
    );

  it('BigInt 로 차분해 2^53 보다 큰 값도 정확히 계산하고 간격은 관측 업타임으로 구한다', () => {
    const series = buildZephyrByteSeries([
      zephyr('2026-11-04T01:00:00Z', '9007199254740993', '0', { uptime: '10000' }),
      // 수신 시각은 1초 차이지만 관측 업타임은 4초 차이다
      zephyr('2026-11-04T01:00:01Z', '9007199254744993', '800', { uptime: '14000' }),
    ]);
    expect(series.rates).toEqual([{ observedAt: '2026-11-04T01:00:01Z', rx: 1000, tx: 200 }]);
    expect(series.rx.bytesPerSecond).toBe(1000);
    expect(series.tx).toEqual({ totalBytes: 800, bytesPerSecond: 200, reason: null });
  });

  it('부팅·publisher·counter_epoch 가 바뀌거나 업타임이 늘지 않으면 차분하지 않는다', () => {
    const base = zephyr('2026-11-04T01:00:00Z', '1000', '1000', { uptime: '1000' });
    const cases = [
      zephyr('2026-11-04T01:00:02Z', '2000', '2000', { uptime: '3000', bootId: 'boot-2' }),
      zephyr('2026-11-04T01:00:02Z', '2000', '2000', { uptime: '3000', publisher: 'pub-2' }),
      zephyr('2026-11-04T01:00:02Z', '2000', '2000', { uptime: '3000', epoch: '2' }),
      zephyr('2026-11-04T01:00:02Z', '2000', '2000', { uptime: '1000' }),
      zephyr('2026-11-04T01:00:02Z', '500', '2000', { uptime: '3000' }),
    ];
    const results = cases.map((next) => buildZephyrByteSeries([base, next]).rates[0]);
    expect(results.map((point) => point?.rx)).toEqual([null, null, null, null, null]);
    expect(results[4]?.tx).toBe(500);
  });

  it('미측정이면 사유를 보여 주고, 실제 0 은 "0" 으로 구분한다', () => {
    const missing = buildZephyrByteSeries([
      zephyr('2026-11-04T01:00:00Z', null, '0', { reason: 'source_unavailable' }),
    ]);
    expect(missing.rx).toEqual({ totalBytes: null, bytesPerSecond: null, reason: 'source_unavailable' });
    expect(missing.tx).toEqual({ totalBytes: 0, bytesPerSecond: null, reason: null });

    expect(buildZephyrByteSeries([]).rx.reason).toBe('데이터 없음');
  });

  it('uint64 범위를 넘거나 십진 정수가 아닌 counter_value 는 읽지 않는다 (networkRate.unsigned)', () => {
    const at = '2026-11-04T01:00:00.000Z';
    expect(buildZephyrByteSeries([zephyr(at, '18446744073709551616', '0')]).rx.totalBytes).toBeNull();
    expect(buildZephyrByteSeries([zephyr(at, '01', '0')]).rx.totalBytes).toBeNull();
    expect(buildZephyrByteSeries([zephyr(at, '0', '0')]).rx.totalBytes).toBe(0);
  });
});

describe('Zephyr 인터페이스 상태', () => {
  const flag = (at: string, value: unknown, reason: string | null = null) =>
    sample(at, [
      metric({ name: ZEPHYR_INTERFACE_UP_METRIC, value: value as number | null, unavailable_reason: reason }),
    ]);

  it('지금 백엔드처럼 value=null·사유 "ok" 면 "ok" 를 보여 주지 않고 백엔드 미지원으로 표시한다', () => {
    expect(interfaceFlagOf([flag('2026-11-04T01:00:00Z', null, 'ok')], ZEPHYR_INTERFACE_UP_METRIC)).toEqual({
      state: 'missing',
      reason: BACKEND_UNSUPPORTED,
    });
    expect(displayReason({ unavailable_reason: null })).toBe(BACKEND_UNSUPPORTED);
    expect(displayReason({ unavailable_reason: 'driver_not_ready' })).toBe('driver_not_ready');
  });

  it('boolean·숫자(0/1)·문자열 값을 받으면 가장 최근 값을 up/down 으로 읽는다', () => {
    const latest = (value: unknown) =>
      interfaceFlagOf(
        [flag('2026-11-04T01:00:00Z', null, 'ok'), flag('2026-11-04T01:00:02Z', value)],
        ZEPHYR_INTERFACE_UP_METRIC,
      ).state;
    expect(latest(true)).toBe('on');
    expect(latest(false)).toBe('off');
    expect(latest(1)).toBe('on');
    expect(latest(0)).toBe('off');
    expect(latest('true')).toBe('on');
    expect(latest(2)).toBe('missing');
  });

  it('지표가 아예 없으면 데이터 없음이다', () => {
    expect(interfaceFlagOf([], ZEPHYR_INTERFACE_UP_METRIC)).toEqual({
      state: 'missing',
      reason: '데이터 없음',
    });
  });
});

describe('Zephyr rx_bytes_per_s', () => {
  it('직접 계측 값을 시계열로 읽고 값이 없으면 사유를 남긴다', () => {
    const rate = (at: string, value: number | null, reason: string | null = null) =>
      sample(at, [metric({ name: 'rx_bytes_per_s', value, unavailable_reason: reason })]);

    const series = buildRxByteRateSeries([
      rate('2026-11-04T01:00:02Z', 2048),
      rate('2026-11-04T01:00:00Z', 1024),
    ]);
    expect(series.points.map((point) => point.value)).toEqual([1024, 2048]);
    expect(series.current).toBe(2048);

    const missing = buildRxByteRateSeries([rate('2026-11-04T01:00:00Z', null, 'rate_not_ready')]);
    expect(missing).toEqual({ points: [], current: null, reason: 'rate_not_ready' });
  });
});

describe('RX 속도 대체값', () => {
  it('counter_value 차분이 있으면 그 값을, 없을 때만 대체 속도를 쓴다', () => {
    const counted = { totalBytes: 1000, bytesPerSecond: 50, reason: null };
    const waiting = { totalBytes: 1000, bytesPerSecond: null, reason: null };
    const missing = { totalBytes: null, bytesPerSecond: null, reason: 'source_unavailable' };

    expect(withRateFallback(counted, 99)).toEqual(counted);
    expect(withRateFallback(waiting, 99)).toEqual({ ...waiting, bytesPerSecond: 99 });
    expect(withRateFallback(waiting, null)).toEqual(waiting);
    expect(withRateFallback(missing, 99)).toEqual(missing);
  });
});

describe('송수신 속도 그래프 대체값', () => {
  const at = (seconds: number) => new Date(Date.UTC(2026, 10, 4, 1, 0, seconds)).toISOString();

  it('차분 점은 그대로 두고, 차분을 못 구한 점만 그 시각 이전 10초 이내 대체 속도로 채운다', () => {
    const merged = mergeRxRateFallback(
      [
        { observedAt: at(2), rx: 100, tx: 10 },
        { observedAt: at(4), rx: null, tx: null },
        { observedAt: at(30), rx: null, tx: 5 },
      ],
      [
        { observedAt: at(1), value: 900 },
        { observedAt: at(3), value: 300 },
      ],
    );
    expect(merged).toEqual([
      { observedAt: at(2), rx: 100, tx: 10 },
      { observedAt: at(4), rx: 300, tx: null },
      { observedAt: at(30), rx: null, tx: 5 },
    ]);
  });

  it('차분 점이 하나도 없으면 대체 시계열을 RX 로 그린다', () => {
    expect(mergeRxRateFallback([], [{ observedAt: at(1), value: 42 }])).toEqual([
      { observedAt: at(1), rx: 42, tx: null },
    ]);
  });
});
