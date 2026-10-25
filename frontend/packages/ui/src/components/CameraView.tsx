import { useEffect, useRef, useState } from 'react';
import styles from './CameraView.module.css';

export interface CameraMotion {
  /** 차량 속도 (m/s) — 시험 영상에서 차선이 흐르는 속도 */
  v: number;
  /** 조향 (rad) — 시험 영상에서 도로가 기우는 정도 */
  steer: number;
  /** 차선 오프셋 (m) */
  laneOffset: number;
  /** 장애물 감지 여부 — 시험 영상에 감지 박스를 그린다 */
  obstacle: boolean;
}

export interface CameraStat {
  label: string;
  value: string;
  warn?: boolean;
}

export interface CameraViewProps {
  /** AI 처리 영상 MJPEG 주소. 없고 mock 이면 시험 영상 */
  streamUrl?: string;
  mock?: boolean;
  /** 영상을 내는 VM 이 꺼져 있으면 그 이유. 있으면 화면을 검게 하고 이유를 띄운다 */
  down?: string | null;
  motion: CameraMotion;
  /** 영상 아래 띠에 겹쳐 쓸 값 */
  stats?: CameraStat[];
  /** 오른쪽 위 경고 (인지 끊김 등) */
  alert?: string | null;
  /** 좁은 칸용: 글자·여백을 줄인다 */
  compact?: boolean;
}

/**
 * 카메라 + AI 감지 화면. 실제는 영상을 받아 AI 로 장애물을 찾는 VM 이 감지 박스를 그린 영상을 MJPEG 로 낸다
 * (주소·형식은 계약 미정). 그 VM 이 죽으면 영상도 멈춘다 — 주행은 그대로 (격리 시연).
 * mock 이면 차량 움직임을 따라 흐르는 도로와 감지 박스를 캔버스로 그린다.
 */
const RETRY_MS = 3000;

export function CameraView({ streamUrl, mock, down, motion, stats = [], alert, compact }: CameraViewProps) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const source = down ? 'down' : streamUrl ? (failed ? 'error' : 'stream') : mock ? 'mock' : 'none';

  // 영상이 끊기면 3초마다 다시 연결한다 (카메라 계약 camera-stream-v1)
  useEffect(() => {
    if (source !== 'error') return;
    const timer = window.setTimeout(() => {
      setFailed(false);
      setAttempt((n) => n + 1);
    }, RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [source, attempt]);

  return (
    <div className={[styles.frame, compact ? styles.compact : ''].join(' ')}>
      {source === 'stream' && (
        <img
          key={attempt}
          className={styles.video}
          src={`${streamUrl}${streamUrl!.includes('?') ? '&' : '?'}t=${attempt}`}
          alt="카메라 AI 감지 영상"
          onError={() => setFailed(true)}
        />
      )}
      {source === 'mock' && <MockVideo motion={motion} />}
      {(source === 'down' || source === 'error' || source === 'none') && (
        <div className={styles.empty}>
          <p>
            {source === 'down'
              ? down
              : source === 'error'
                ? '영상 없음 — 3초 뒤 다시 연결'
                : '영상 주소가 없다 (VITE_CAMERA_URL)'}
          </p>
          {source === 'error' && (
            <button
              type="button"
              className={styles.retry}
              onClick={() => {
                setFailed(false);
                setAttempt((n) => n + 1);
              }}
            >
              다시 연결
            </button>
          )}
        </div>
      )}
      {(source === 'stream' || source === 'mock') && (
        <>
          <span className={styles.tag}>
            <i className={styles.rec} /> {source === 'mock' ? 'MOCK · AI 감지' : 'LIVE · AI 감지'}
          </span>
          {alert && <span className={styles.alert}>{alert}</span>}
          {stats.length > 0 && (
            <dl className={styles.overlay}>
              {stats.map((s) => (
                <div key={s.label}>
                  <dt>{s.label}</dt>
                  <dd className={s.warn ? styles.warn : undefined}>{s.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </>
      )}
    </div>
  );
}

/** 시험 영상: 앞을 내려다본 도로 + AI 감지 표시(차선 초록 선, 장애물 빨간 박스와 신뢰도) */
function MockVideo({ motion }: { motion: CameraMotion }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(motion);
  useEffect(() => {
    latest.current = motion;
  });

  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    let frame = 0;
    let travelled = 0;
    let last = performance.now();
    let obstacleZ = 1;
    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

    const draw = (now: number) => {
      const { v, steer, laneOffset, obstacle } = latest.current;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      travelled += Math.abs(v) * dt;
      const w = element.width;
      const h = element.height;
      const horizon = h * 0.38;
      const shift = (-laneOffset * 0.9 - steer * 0.5) * w;
      const vx = w / 2 + shift * 0.3;
      const edge = (side: number, t: number) =>
        lerp(vx + side * w * 0.02, w / 2 + shift + side * w * 0.45, t);

      ctx.fillStyle = '#0b1220';
      ctx.fillRect(0, 0, w, horizon);
      ctx.fillStyle = '#1a2330';
      ctx.fillRect(0, horizon, w, h - horizon);
      ctx.fillStyle = '#2a313b';
      ctx.beginPath();
      ctx.moveTo(edge(-1, 0), horizon);
      ctx.lineTo(edge(1, 0), horizon);
      ctx.lineTo(edge(1, 1), h);
      ctx.lineTo(edge(-1, 1), h);
      ctx.closePath();
      ctx.fill();

      // 도로 경계
      ctx.strokeStyle = '#dee2ed';
      ctx.lineWidth = 3;
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(edge(side, 0), horizon);
        ctx.lineTo(edge(side, 1), h);
        ctx.stroke();
      }
      // 가운데 점선 (원근으로 간격이 벌어진다)
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
      // AI 차선 감지 — 경계 안쪽에 초록 반투명 띠
      ctx.strokeStyle = 'rgba(78, 222, 163, 0.85)';
      ctx.lineWidth = 4;
      ctx.setLineDash([]);
      for (const side of [-0.92, 0.92]) {
        ctx.beginPath();
        ctx.moveTo(lerp(vx, edge(side > 0 ? 1 : -1, 0), Math.abs(side)), horizon + 20);
        ctx.lineTo(lerp(w / 2 + shift, edge(side > 0 ? 1 : -1, 1), Math.abs(side)), h);
        ctx.stroke();
      }
      ctx.fillStyle = 'rgba(78, 222, 163, 0.9)';
      ctx.font = '600 15px monospace';
      ctx.fillText('lane 0.97', edge(-1, 0.55) + 18, lerp(horizon, h, 0.55));

      // 장애물: 멀리서 다가오다 가까운 곳에서 멈춘다 + 감지 박스
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
        ctx.fillRect(ox - size / 2 - 7, oy - size - 30, 150, 24);
        ctx.fillStyle = '#fff';
        ctx.font = '700 15px monospace';
        ctx.fillText(
          `obstacle ${(0.9 + Math.sin(now / 400) * 0.04).toFixed(2)}`,
          ox - size / 2 - 1,
          oy - size - 12,
        );
      } else {
        obstacleZ = 1;
      }

      ctx.fillStyle = 'rgba(255,255,255,0.03)';
      for (let i = 0; i < 40; i++) ctx.fillRect(Math.random() * w, Math.random() * h, 2, 2);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <canvas
      ref={canvas}
      className={styles.video}
      width={960}
      height={540}
      aria-label="시험 카메라 AI 감지 영상"
    />
  );
}
