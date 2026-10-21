import { Button, Card, StatusBadge } from '@xenova/ui';
import { useEffect, useRef, useState } from 'react';
import type { DriveState } from '../api/drive';
import { signed } from '../domain/roles';
import styles from './CameraFeed.module.css';

/**
 * 젯슨 카메라 영상 (대시보드). 영상은 MQTT 로 오지 않는다 — 젯슨이 따로 여는 영상 스트림을 브라우저가 바로 받는다.
 *   VITE_CAMERA_URL: MJPEG 스트림 주소 (예: http://jetson.local:8080/stream.mjpg). 형식·주소는 젯슨 담당과 계약 미정.
 *   VITE_MOCK 이고 주소가 없으면 시험 영상(캔버스)을 그린다 — 차량 속도·조향·장애물을 따라 움직인다.
 * 영상 위에는 인지 결과(perception.result 요약)를 겹쳐 쓴다. 영상과 인지 결과는 경로가 달라 시각이 조금 어긋날 수 있다.
 */
const CAMERA_URL: string = import.meta.env.VITE_CAMERA_URL ?? '';
const MOCK = import.meta.env.VITE_MOCK === 'true';

type FeedState = 'loading' | 'live' | 'error';

export function CameraFeed({ drive }: { drive: DriveState | null }) {
  const perception = drive?.perception;
  const [state, setState] = useState<FeedState>(CAMERA_URL ? 'loading' : 'live');
  const [attempt, setAttempt] = useState(0);
  const source = CAMERA_URL ? 'stream' : MOCK ? 'mock' : 'none';

  const badge =
    source === 'none'
      ? { health: 'stopped' as const, label: '주소 미설정' }
      : state === 'error'
        ? { health: 'fault' as const, label: '영상 없음' }
        : state === 'loading'
          ? { health: 'recovering' as const, label: '연결 중' }
          : { health: 'ok' as const, label: source === 'mock' ? '시험 영상' : '실시간' };

  return (
    <Card title="카메라 (젯슨)" actions={<StatusBadge health={badge.health} label={badge.label} size="sm" />}>
      <div className={styles.frame}>
        {source === 'stream' && state !== 'error' && (
          <img
            key={attempt}
            className={styles.video}
            src={`${CAMERA_URL}${CAMERA_URL.includes('?') ? '&' : '?'}t=${attempt}`}
            alt="젯슨 카메라 실시간 영상"
            onLoad={() => setState('live')}
            onError={() => setState('error')}
          />
        )}
        {source === 'mock' && <MockVideo drive={drive} />}
        {(source === 'none' || state === 'error') && (
          <div className={styles.empty}>
            <p>
              {source === 'none'
                ? '카메라 스트림 주소가 없다 (VITE_CAMERA_URL)'
                : '카메라 영상을 받지 못했다'}
            </p>
            {source === 'stream' && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setState('loading');
                  setAttempt((n) => n + 1);
                }}
              >
                다시 연결
              </Button>
            )}
          </div>
        )}

        {source !== 'none' && state !== 'error' && (
          <>
            <span className={styles.tag}>
              <i className={styles.rec} /> {source === 'mock' ? 'MOCK' : 'LIVE'}
            </span>
            {perception && !perception.valid && (
              <span className={styles.lost}>인지 끊김 — 자율주행 정지 명령</span>
            )}
            <dl className={styles.overlay}>
              <div>
                <dt>차선</dt>
                <dd>{perception?.lane_offset_m == null ? '—' : `${signed(perception.lane_offset_m)} m`}</dd>
              </div>
              <div>
                <dt>장애물</dt>
                <dd className={perception?.obstacle_count ? styles.warn : undefined}>
                  {perception?.obstacle_count ?? '—'}
                </dd>
              </div>
              <div>
                <dt>정지 필요</dt>
                <dd className={perception?.stop ? styles.warn : undefined}>
                  {perception?.stop ? '예' : '아니오'}
                </dd>
              </div>
            </dl>
          </>
        )}
      </div>
      <p className={styles.hint}>
        영상은 젯슨에서 바로 오고 VM 을 거치지 않는다 — VM 을 강제 종료해도 영상은 계속 나온다. 겹친 값은 젯슨
        인지 결과(perception.result).
      </p>
    </Card>
  );
}

/** 시험 영상: 앞을 내려다본 도로. 속도만큼 차선이 흘러가고, 조향·차선 오프셋만큼 도로가 기운다 */
function MockVideo({ drive }: { drive: DriveState | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(drive);
  useEffect(() => {
    latest.current = drive;
  });

  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    let frame = 0;
    let travelled = 0;
    let last = performance.now();
    let obstacleZ = 1;

    const draw = (now: number) => {
      const d = latest.current;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const v = d?.vehicle?.v_mps ?? 0;
      const steer = d?.vehicle?.steer_rad ?? 0;
      const offset = d?.perception?.valid ? (d.perception.lane_offset_m ?? 0) : 0;
      const obstacle = !!d?.vehicle?.flags?.obstacle || (d?.perception?.obstacle_count ?? 0) > 0;
      travelled += Math.abs(v) * dt;

      const w = element.width;
      const h = element.height;
      const horizon = h * 0.38;
      const shift = (-offset * 0.9 - steer * 0.5) * w;
      ctx.fillStyle = '#0b1220';
      ctx.fillRect(0, 0, w, horizon);
      ctx.fillStyle = '#1a2330';
      ctx.fillRect(0, horizon, w, h - horizon);

      // 도로 (소실점으로 모이는 사다리꼴)
      const vx = w / 2 + shift * 0.3;
      ctx.fillStyle = '#2a313b';
      ctx.beginPath();
      ctx.moveTo(vx - w * 0.02, horizon);
      ctx.lineTo(vx + w * 0.02, horizon);
      ctx.lineTo(w / 2 + shift + w * 0.45, h);
      ctx.lineTo(w / 2 + shift - w * 0.45, h);
      ctx.closePath();
      ctx.fill();

      const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
      const edge = (side: number, t: number) =>
        lerp(vx + side * w * 0.02, w / 2 + shift + side * w * 0.45, t);
      ctx.strokeStyle = '#dee2ed';
      ctx.lineWidth = 3;
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(edge(side, 0), horizon);
        ctx.lineTo(edge(side, 1), h);
        ctx.stroke();
      }
      // 가운데 점선 — 원근으로 간격이 벌어진다
      ctx.strokeStyle = '#f59e0b';
      for (let i = 0; i < 12; i++) {
        const z = (i + ((travelled * 2) % 1)) / 12;
        const t0 = z * z;
        const t1 = Math.min(1, (z + 0.04) * (z + 0.04));
        ctx.lineWidth = 1 + t0 * 6;
        ctx.beginPath();
        ctx.moveTo(lerp(vx, w / 2 + shift, t0), lerp(horizon, h, t0));
        ctx.lineTo(lerp(vx, w / 2 + shift, t1), lerp(horizon, h, t1));
        ctx.stroke();
      }

      // 장애물: 멀리서 다가오다 가까운 곳에 멈춘다
      if (obstacle) {
        obstacleZ = Math.max(0.55, obstacleZ - dt * 0.4);
        const t = 1 - obstacleZ + 0.2;
        const ox = lerp(vx, w / 2 + shift, t) + w * 0.06 * t;
        const oy = lerp(horizon, h, t);
        const size = 30 + t * 120;
        ctx.fillStyle = '#8b919d';
        ctx.fillRect(ox - size / 2, oy - size, size, size);
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 3;
        ctx.strokeRect(ox - size / 2 - 6, oy - size - 6, size + 12, size + 12);
        ctx.fillStyle = '#ef4444';
        ctx.font = '600 16px monospace';
        ctx.fillText('obstacle', ox - size / 2 - 6, oy - size - 12);
      } else {
        obstacleZ = 1;
      }

      // 화면 잡음 (카메라 느낌)
      ctx.fillStyle = 'rgba(255,255,255,0.03)';
      for (let i = 0; i < 40; i++) ctx.fillRect(Math.random() * w, Math.random() * h, 2, 2);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <canvas ref={canvas} className={styles.video} width={960} height={540} aria-label="시험 카메라 영상" />
  );
}
