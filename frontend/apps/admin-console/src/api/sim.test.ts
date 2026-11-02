import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setSession } from './client';
import {
  createSimResultWaiter,
  describeSimResult,
  placeSimObstacle,
  selectSimTrack,
  SimCommandError,
  SIM_NO_RESPONSE,
  SIM_RESULT_TIMEOUT_MS,
  type SimCommandResult,
} from './sim';

const result = (over: Partial<SimCommandResult>): SimCommandResult => ({
  request_id: 'r-1',
  op: 'obstacle.add',
  ok: true,
  code: null,
  message: null,
  ...over,
});

describe('시뮬레이터 명령 결과 짝짓기 (WS-06 sim.command.result)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('같은 request_id 결과만 받는다', async () => {
    const waiter = createSimResultWaiter();
    const got = waiter.wait('r-1', 'obstacle.add');
    waiter.deliver(result({ request_id: 'r-other', ok: false, code: 'ON_CAR' }));
    waiter.deliver(
      result({ request_id: 'r-1', ok: false, code: 'OUT_OF_TRACK', message: '벽 밖에는 놓을 수 없어요' }),
    );
    await expect(got).resolves.toMatchObject({ request_id: 'r-1', code: 'OUT_OF_TRACK' });
  });

  it('REST 응답보다 결과가 먼저 와도 짝짓는다', async () => {
    const waiter = createSimResultWaiter();
    waiter.deliver(result({ request_id: 'r-2' }));
    await expect(waiter.wait('r-2', 'obstacle.add')).resolves.toMatchObject({ request_id: 'r-2', ok: true });
  });

  it('3초 안에 결과가 없으면 "시뮬레이터 응답 없음"', async () => {
    const waiter = createSimResultWaiter();
    const got = waiter.wait('r-3', 'push');
    vi.advanceTimersByTime(SIM_RESULT_TIMEOUT_MS - 1);
    waiter.deliver(result({ request_id: 'r-x' }));
    vi.advanceTimersByTime(1);
    const r = await got;
    expect(r).toEqual({
      request_id: 'r-3',
      op: 'push',
      ok: false,
      code: 'NO_RESPONSE',
      message: SIM_NO_RESPONSE,
    });
    expect(describeSimResult(r)).toBe('시뮬레이터 응답 없음');
  });

  it('응답 없음 뒤 늦게 온 결과는 다음 기다림에 섞이지 않는다', async () => {
    const waiter = createSimResultWaiter();
    const late = waiter.wait('r-4', 'push');
    vi.advanceTimersByTime(SIM_RESULT_TIMEOUT_MS);
    await expect(late).resolves.toMatchObject({ code: 'NO_RESPONSE' });
    waiter.deliver(result({ request_id: 'r-4' })); // 늦게 온 결과 — 잠깐 들고 있다가 버린다
    vi.advanceTimersByTime(SIM_RESULT_TIMEOUT_MS);
    const next = waiter.wait('r-4', 'push');
    vi.advanceTimersByTime(SIM_RESULT_TIMEOUT_MS);
    await expect(next).resolves.toMatchObject({ code: 'NO_RESPONSE' });
  });
});

describe('결과 문구', () => {
  it('시뮬레이터 message 를 그대로 띄운다 (계약 §12.1)', () => {
    expect(describeSimResult(result({ ok: false, code: 'ON_CAR', message: '차와 겹치는 자리예요' }))).toBe(
      '차와 겹치는 자리예요',
    );
  });

  it('message 가 없으면 사유 코드를 한국어로', () => {
    const text = (code: string) => describeSimResult(result({ ok: false, code }));
    expect(text('ON_CAR')).toBe('차와 겹치는 자리라 놓을 수 없어요');
    expect(text('OUT_OF_TRACK')).toBe('벽 밖이라 놓을 수 없어요');
    expect(text('INVALID')).toBe('시뮬레이터가 받을 수 없는 명령이에요');
    expect(text('NO_GAP')).toBe('차가 지나갈 틈이 없어요');
    expect(text('TOO_DENSE')).toContain('촘촘');
    expect(text('TOO_CLOSE')).toContain('피할 거리');
    expect(text('SOMETHING_NEW')).toBe('시뮬레이터가 거부했어요 (SOMETHING_NEW)');
  });

  it('적용은 명령별 짧은 문구', () => {
    expect(describeSimResult(result({ op: 'obstacle.add' }))).toBe('장애물을 놓았어요');
    expect(describeSimResult(result({ op: 'track.select' }))).toBe('트랙을 바꿨어요');
    expect(describeSimResult(result({ op: 'unknown' }))).toBe('적용했어요');
  });
});

describe('장애물 놓기 (REST-31 → WS-06)', () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', fetchMock);
    setSession({
      access_token: 't',
      token_type: 'Bearer',
      expires_in: 900,
      expires_at: '2026-10-30T00:15:00Z',
      user: { user_id: 'u-1', username: 'admin', nickname: '관리자' },
    });
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    setSession(null);
  });

  it('202 의 request_id 로 결과를 기다리고, 오지 않으면 3초 뒤 응답 없음', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ obstacle_id: 'o-1', request_id: 'req-9' }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const got = placeSimObstacle('cone', 1, 2);
    await vi.advanceTimersByTimeAsync(SIM_RESULT_TIMEOUT_MS);
    await expect(got).resolves.toMatchObject({
      request_id: 'req-9',
      op: 'obstacle.add',
      code: 'NO_RESPONSE',
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain('/api/v1/sim/obstacles');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ kind: 'cone', x: 1, y: 2 });
  });
  it('트랙 바꾸기(Promise<void>)는 응답 없음을 SimCommandError 로 던진다 — 대시보드 catch 가 그대로 띄운다', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ request_id: 'req-10' }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const got = selectSimTrack('monza');
    const caught = got.catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(SIM_RESULT_TIMEOUT_MS);
    const e = await caught;
    expect(e).toBeInstanceOf(SimCommandError);
    expect((e as SimCommandError).message).toBe('시뮬레이터 응답 없음');
    expect((e as SimCommandError).result).toMatchObject({ request_id: 'req-10', op: 'track.select' });
  });
});
