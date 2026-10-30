import { describe, expect, it } from 'vitest';
import { appendLogs, parseLogPage, type RuntimeLog } from './logs';

function row(id: number): RuntimeLog {
  return {
    id,
    event_id: String(id),
    source_id: 'dom0',
    vm_id: null,
    component: 'api.service',
    boot_id: 'boot',
    observed_at: '2026-10-28T00:00:00Z',
    received_at: 1,
    level: 'INFO',
    message: `<script>${id}</script>`,
  };
}
describe('runtime log pages', () => {
  it('deduplicates overlap and caps the browser buffer', () => {
    const records = Array.from({ length: 1100 }, (_, i) => row(i + 1));
    const result = appendLogs(records, [row(1100), row(1101)]);
    expect(result).toHaveLength(1000);
    expect(result.at(-1)?.id).toBe(1101);
  });
  it('preserves plain text without executing or stripping log content', () => {
    const page = parseLogPage({
      epoch: 'one',
      next_cursor: 1,
      has_more: false,
      history_truncated: false,
      items: [row(1)],
    });
    expect(page.items[0]?.message).toBe('<script>1</script>');
  });
  it.each([null, {}, { epoch: 'a', items: [], next_cursor: -1, has_more: false, history_truncated: false }])(
    'rejects incomplete or invalid pages',
    (value) => {
      expect(() => parseLogPage(value)).toThrow();
    },
  );
});
