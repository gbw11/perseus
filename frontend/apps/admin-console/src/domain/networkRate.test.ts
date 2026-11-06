import { describe, expect, it } from 'vitest';
import type { MetricSample, MetricValue } from '../api/types';
import { networkRxKiBPerSecond } from './networkRate';

function sample(time: number, metric: Partial<MetricValue>, epoch = '1'): MetricSample {
  return {
    sample_id: String(time),
    vm_id: 'vm',
    boot_id: 'boot',
    observed_at: new Date(time).toISOString(),
    source: 'fixture',
    network: { publisher_instance_id: 'publisher', observed_uptime_ms: String(time) },
    values: [
      {
        name: 'net_rx_bytes_total',
        unit: 'bytes',
        value: null,
        unavailable_reason: null,
        labels: { iface: 'eth0', counter_epoch: epoch },
        ...metric,
      },
    ],
  };
}

describe('network RX in KiB/s', () => {
  it('subtracts exact Zephyr counters and skips duplicate cached observations', () => {
    const first = sample(1000, { counter_value: '9007199254740993' });
    const next = sample(2000, { counter_value: '9007199254742017' });
    expect(networkRxKiBPerSecond([first, next, next], 'zephyr')).toEqual([null, 1]);
  });

  it('starts a new baseline after epoch, publisher and boot changes or missing measurements', () => {
    const at = (time: number, counter_value: string, epoch?: string) =>
      sample(time, { counter_value }, epoch);
    const missing = sample(8000, { unavailable_reason: 'read_failed' }, '2');
    const publisher = at(4000, '4096', '2');
    publisher.network!.publisher_instance_id = 'publisher-2';
    const boot = { ...at(5000, '5120', '2'), boot_id: 'boot-2' };
    expect(
      networkRxKiBPerSecond(
        [
          at(1000, '1024'),
          at(2000, '2048'),
          at(3000, '3072', '2'),
          publisher,
          boot,
          at(6000, '6144', '2'),
          at(7000, '7168', '2'),
          missing,
          at(9000, '9216', '2'),
        ],
        'zephyr',
      ),
    ).toEqual([null, 1, null, null, null, null, 1, null, null]);
  });

  it('prefers reported Zephyr rates and keeps zero distinct from an unavailable report', () => {
    const rate = { name: 'rx_bytes_per_s', unit: 'bytes/s' };
    expect(
      networkRxKiBPerSecond(
        [
          sample(1000, { counter_value: '1024' }),
          sample(2000, { ...rate, value: 2048 }),
          sample(3000, { ...rate, value: 0 }),
          sample(4000, { ...rate, unavailable_reason: 'first_sample' }),
        ],
        'zephyr',
      ),
    ).toEqual([2, 0, null]);
  });

  it('handles Linux counters per interface and boot, including resets', () => {
    expect(
      networkRxKiBPerSecond(
        [
          sample(1000, { value: 10_240 }),
          sample(2000, { value: 11_264 }),
          sample(3000, { value: 512 }),
          sample(4000, { value: 512 }),
          sample(5000, { value: 1024, labels: { iface: 'ens3' } }),
          sample(6000, { value: 2048, labels: { iface: 'ens3' } }),
        ],
        'linux',
      ),
    ).toEqual([null, null, null, null, null, 1]);
    expect(
      networkRxKiBPerSecond(
        [
          sample(1000, { value: 1024 }),
          sample(2000, { value: 2048 }),
          sample(3000, { value: 0 }),
          sample(4000, { value: 0 }),
        ],
        'linux',
      ),
    ).toEqual([null, 1, null, 0]);
  });
});
