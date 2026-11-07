import { at, realM, STAND_SIDE, wallOf, type Circuit } from './circuit';
import { OBSTACLE_H, OBSTACLE_NAME, SCENE, tireStack, treeImage } from './paint';
import { runoffColor } from './renderTop';
import type { SimObstacle, SimPose } from './types';

export interface PovScene {
  pose: SimPose;
  obstacles: SimObstacle[];
  /** 출발 신호 다리에 켤 빨간 불 수 (0~5) */
  lightsOn: number;
  /** 장애물 인식 박스 (종류·실제 거리) — 자율주행 칸 */
  boxes: boolean;
  /** 'low' 면 나무·관중·구름을 빼고 그린다 (보드 브라우저가 느릴 때) */
  quality: 'high' | 'low';
}

/** 카메라: 차 앞 0.12 m, 높이 0.2 m, 아래 7°, 수평 화각 75°. 10 m 까지만 그린다 */
const CH = 0.2;
const PITCH = (7 * Math.PI) / 180;
const HALF_FOV = (37.5 * Math.PI) / 180;
const FAR = 10;
const NEAR = 0.04;
/** 인식 박스를 그리는 거리 (인지 대역 인식 거리 4 m) */
const LOOK_M = 4;

type Cam = { z: number; l: number; dn: number; f: number };
type Item = { z: number; draw: () => void };

interface Cloud {
  c: HTMLCanvasElement;
  az: number;
  el: number;
  s: number;
}
let clouds: Cloud[] | null = null;
/** 구름: 부드러운 흰 덩어리를 한 번만 그려 둔다. 방위각에 붙어 있어 차가 돌면 같이 돈다 */
function cloudSprites(): Cloud[] {
  if (clouds) return clouds;
  let q = 21;
  const rr = () => (q = (q * 16807) % 2147483647) / 2147483647;
  clouds = [];
  for (let k = 0; k < 14; k++) {
    const cw = Math.round(260 + rr() * 260);
    const ch = Math.round(cw * 0.38);
    const c = document.createElement('canvas');
    c.width = cw;
    c.height = ch;
    const g = c.getContext('2d');
    if (g) {
      for (let n = 0; n < 26; n++) {
        const x = cw * (0.15 + rr() * 0.7);
        const y = ch * (0.45 + rr() * 0.3);
        const r = ch * (0.18 + rr() * 0.22);
        const gr = g.createRadialGradient(x, y - r * 0.3, r * 0.1, x, y, r);
        gr.addColorStop(0, 'rgb(255 255 255 / 0.9)');
        gr.addColorStop(0.6, 'rgb(240 244 250 / 0.55)');
        gr.addColorStop(1, 'rgb(225 232 242 / 0)');
        g.fillStyle = gr;
        g.beginPath();
        g.arc(x, y, r, 0, Math.PI * 2);
        g.fill();
      }
      g.globalCompositeOperation = 'source-atop';
      const sh = g.createLinearGradient(0, 0, 0, ch);
      sh.addColorStop(0.5, 'rgb(0 0 0 / 0)');
      sh.addColorStop(1, 'rgb(120 135 160 / 0.35)');
      g.fillStyle = sh;
      g.fillRect(0, 0, cw, ch);
    }
    clouds.push({ c, az: rr() * Math.PI * 2, el: 0.05 + rr() * 0.22, s: 0.5 + rr() * 0.6 });
  }
  return clouds;
}

function drawSky(
  g: CanvasRenderingContext2D,
  W: number,
  H: number,
  cx: number,
  F: number,
  horizon: number,
  yaw: number,
  high: boolean,
) {
  const sky = g.createLinearGradient(0, 0, 0, horizon);
  sky.addColorStop(0, SCENE.skyTop);
  sky.addColorStop(0.75, '#7fb0e0');
  sky.addColorStop(1, SCENE.skyLow);
  g.fillStyle = sky;
  g.fillRect(0, 0, W, horizon + 1);
  const rel = (a: number) => Math.atan2(Math.sin(a - yaw), Math.cos(a - yaw));
  const HF = Math.atan(cx / F) + 0.3;
  {
    const r = rel(2.2);
    if (Math.abs(r) < HF) {
      const u = cx - F * Math.tan(r);
      const v = horizon - F * Math.tan(0.35);
      const gl = g.createRadialGradient(u, v, 0, u, v, W * 0.25);
      gl.addColorStop(0, 'rgb(255 250 230 / 0.9)');
      gl.addColorStop(0.08, 'rgb(255 245 220 / 0.6)');
      gl.addColorStop(1, 'rgb(255 245 220 / 0)');
      g.fillStyle = gl;
      g.fillRect(0, 0, W, horizon);
    }
  }
  if (high)
    for (const c of cloudSprites()) {
      const r = rel(c.az);
      if (Math.abs(r) > HF) continue;
      const u = cx - F * Math.tan(r);
      const v = horizon - F * Math.tan(c.el);
      const w = (c.c.width * c.s * W) / 900;
      const h = (c.c.height * c.s * W) / 900;
      g.globalAlpha = 0.95;
      g.drawImage(c.c, u - w / 2, v - h / 2, w, h);
      g.globalAlpha = 1;
    }
  // 먼 산 → 가까운 숲 띠 (방위각 함수라 돌면 같이 돈다)
  for (const [col, amp, base, f1, f2] of [
    [SCENE.hill, 0.05, 0.012, 2, 5],
    [SCENE.hill2, 0.022, 0.006, 7, 13],
  ] as const) {
    g.fillStyle = col;
    g.beginPath();
    g.moveTo(0, horizon + 2);
    for (let x = 0; x <= W; x += 6) {
      const a = yaw + Math.atan((cx - x) / F);
      const hh = base + amp * (0.5 + 0.3 * Math.sin(a * f1 + 1) + 0.2 * Math.sin(a * f2 + 4));
      g.lineTo(x, horizon - F * hh);
    }
    g.lineTo(W, horizon + 2);
    g.closePath();
    g.fill();
  }
  const gr = g.createLinearGradient(0, horizon, 0, H);
  gr.addColorStop(0, '#5c7a4a');
  gr.addColorStop(0.25, SCENE.field2);
  gr.addColorStop(1, SCENE.field);
  g.fillStyle = gr;
  g.fillRect(0, horizon, W, H - horizon);
}

/**
 * 차 1인칭 — 트랙 조각을 카메라 앞(z ≥ NEAR)만 잘라 먼 것부터 그린다.
 * 코너에서 차 옆 조각이 카메라 뒤에 걸쳐도 통째로 버리지 않아 검은 틈이 생기지 않는다.
 */
export function drawPov(g: CanvasRenderingContext2D, W: number, H: number, c: Circuit, scene: PovScene) {
  const { pose, obstacles, lightsOn, boxes: showBoxes, quality } = scene;
  const layout = c.track.layout;
  const camera = layout?.camera;
  const high = quality === 'high' && !layout;
  const pitch = camera?.pitch_rad ?? PITCH;
  const cameraHeight = camera?.height_m ?? CH;
  const F = W / 2 / Math.tan(camera ? camera.hfov_rad / 2 : HALF_FOV);
  const cx = W / 2;
  const cy = H * (layout ? 0.5 : 0.44);
  const horizon = cy - F * Math.tan(pitch);
  if (layout) {
    g.fillStyle = '#101216';
    g.fillRect(0, 0, W, H);
  } else drawSky(g, W, H, cx, F, horizon, pose.yaw, high);
  const cs = Math.cos(pose.yaw);
  const sn = Math.sin(pose.yaw);
  const camx = pose.x + (camera?.mount_x_m ?? 0.12) * cs;
  const camy = pose.y + (camera?.mount_x_m ?? 0.12) * sn;
  const cP = Math.cos(pitch);
  const sP = Math.sin(pitch);
  const cam = (x: number, y: number, h = 0): Cam => {
    const dx = x - camx;
    const dy = y - camy;
    const f = dx * cs + dy * sn;
    const l = -dx * sn + dy * cs;
    const up = h - cameraHeight;
    return { z: f * cP - up * sP, l, dn: -up * cP - f * sP, f };
  };
  const toScreen = (p: Cam) => ({ u: cx - (F * p.l) / p.z, v: cy + (F * p.dn) / p.z, z: p.z });
  const proj = (x: number, y: number, h = 0) => {
    const p = cam(x, y, h);
    return p.z < NEAR || p.f > FAR ? null : toScreen(p);
  };
  const clipDraw = (pts: Cam[], fill: string, edge?: string) => {
    const out: Cam[] = [];
    for (let k = 0; k < pts.length; k++) {
      const a = pts[k]!;
      const b = pts[(k + 1) % pts.length]!;
      const ain = a.z >= NEAR;
      const bin = b.z >= NEAR;
      if (ain) out.push(a);
      if (ain !== bin) {
        const t = (NEAR - a.z) / (b.z - a.z);
        out.push({ z: NEAR, l: a.l + (b.l - a.l) * t, dn: a.dn + (b.dn - a.dn) * t, f: 0 });
      }
    }
    if (out.length < 3) return;
    g.beginPath();
    out.forEach((p, k) => {
      const q = toScreen(p);
      if (k) g.lineTo(q.u, q.v);
      else g.moveTo(q.u, q.v);
    });
    g.closePath();
    g.fillStyle = fill;
    g.fill();
    g.strokeStyle = edge ?? fill;
    g.lineWidth = 1;
    g.stroke();
  };
  /** 트랙을 따라 i→j 사이 판: i 쪽 오프셋 a·d, j 쪽 b·c, 높이 h0(a·b)~h1(c·d) */
  const quad = (i: number, j: number, oa: number, ob: number, oc: number, od: number, h0 = 0, h1 = h0) => {
    const A = at(c, i, oa);
    const B = at(c, j, ob);
    const C2 = at(c, j, oc);
    const D = at(c, i, od);
    return [cam(A[0], A[1], h0), cam(B[0], B[1], h0), cam(C2[0], C2[1], h1), cam(D[0], D[1], h1)];
  };
  const visible = (q: Cam[]) => q.some((p) => p.z >= NEAR) && Math.min(...q.map((p) => p.f)) < FAR;
  const depth = (q: Cam[]) => q.reduce((a, p) => a + p.f, 0) / q.length;

  const items: Item[] = [];
  if (layout) {
    // 같은 지면과 테이프를 원근 투영하며 실물 stereo coverage를 대신하지 않는다.
    for (let i = 0; i < c.N; i++) {
      clipDraw(quad(i, (i + 1) % c.N, c.road, c.road, -c.road, -c.road), '#24282f');
    }
    const tape = (points: [number, number][]) => {
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1]!;
        const b = points[i]!;
        const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (!length) continue;
        const nx = ((b[1] - a[1]) / length) * 0.0125;
        const ny = (-(b[0] - a[0]) / length) * 0.0125;
        clipDraw(
          [
            cam(a[0] + nx, a[1] + ny, 0.001),
            cam(b[0] + nx, b[1] + ny, 0.001),
            cam(b[0] - nx, b[1] - ny, 0.001),
            cam(a[0] - nx, a[1] - ny, 0.001),
          ],
          '#f1f3f5',
        );
      }
    };
    for (const side of [-1, 1]) {
      const edge = c.C.map((_, i) => at(c, i, side * c.road));
      tape([...edge, edge[0]!]);
    }
    // 연결부 지면은 도로 경계를 덮고, 주차 테이프는 그 위에 놓는다.
    for (const area of layout.paved_areas)
      clipDraw(
        area.map(([x, y]) => cam(x, y)),
        '#24282f',
      );
    for (const line of layout.paint_lines) tape(line);
    for (let i = 0; i < c.N; i++) {
      const a = c.C[i]!;
      const b = c.C[(i + 1) % c.N]!;
      const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const start = c.SL[i]!;
      const end = start + length;
      const point = (s: number): [number, number] => [
        a[0] + ((b[0] - a[0]) * (s - start)) / length,
        a[1] + ((b[1] - a[1]) * (s - start)) / length,
      ];
      // 샘플 간격과 무관하게25cm 경계에서 파선을 자른다.
      for (let s = start; s < end - 1e-10;) {
        const phase = Math.floor((s + 1e-10) / 0.25);
        const next = Math.min(end, (phase + 1) * 0.25);
        if (phase % 2 === 0) tape([point(s), point(next)]);
        s = next;
      }
    }
    tape([at(c, c.start, -c.road), at(c, c.start, c.road)]);
  }
  for (let i = 0; !layout && i < c.N; i++) {
    const j = (i + 1) % c.N;
    const road = quad(i, j, c.laneW / 2, c.laneW / 2, -c.laneW / 2, -c.laneW / 2);
    if (!visible(road) && !visible(quad(i, j, c.WL[i]!, c.WL[j]!, -c.WR[j]!, -c.WR[i]!))) continue;
    items.push({
      z: depth(road) + 0.02,
      draw: () => {
        for (const sd of [1, -1]) {
          clipDraw(
            quad(i, j, sd * c.road, sd * c.road, sd * wallOf(c, j, sd), sd * wallOf(c, i, sd)),
            runoffColor(c, i, sd),
          );
          clipDraw(
            quad(i, j, (sd * c.laneW) / 2, (sd * c.laneW) / 2, sd * c.road, sd * c.road),
            c.corner[i] ? (i % 2 ? SCENE.curbB : SCENE.curbA) : SCENE.asphalt,
          );
        }
        clipDraw(road, SCENE.asphalt);
        const m0 = proj(...at(c, i, 0));
        const m1 = proj(...at(c, j, 0));
        if (m0 && m1 && i % 4 < 2) {
          g.strokeStyle = SCENE.dash;
          g.globalAlpha = 0.7;
          g.lineWidth = Math.max(1, W / 350 / m0.z);
          g.beginPath();
          g.moveTo(m0.u, m0.v);
          g.lineTo(m1.u, m1.v);
          g.stroke();
          g.globalAlpha = 1;
        }
      },
    });
    for (const sd of [1, -1]) {
      const w = quad(
        i,
        j,
        sd * wallOf(c, i, sd),
        sd * wallOf(c, j, sd),
        sd * wallOf(c, j, sd),
        sd * wallOf(c, i, sd),
        0,
        0.12,
      );
      if (visible(w))
        items.push({
          z: depth(w),
          draw: () =>
            clipDraw(w, c.corner[i] && Math.floor(i / 2) % 2 ? SCENE.curbA : SCENE.wall, SCENE.wallTop),
        });
    }
    for (let row = 0; row < c.standRows[i]!; row++) {
      const di = (c.WR[i]! + 0.25 + row * 0.16) * STAND_SIDE;
      const dj = (c.WR[j]! + 0.25 + row * 0.16) * STAND_SIDE;
      const h = 0.15 + row * 0.12;
      const q = quad(i, j, di, dj, dj, di, h, h + 0.12);
      if (visible(q))
        items.push({
          z: depth(q) - 0.001 * row,
          draw: () => clipDraw(q, row % 2 ? '#2b313b' : SCENE.stand, '#252a33'),
        });
    }
  }
  // 출발 신호 다리: 출발선 위 들보와 빨간 불 다섯 개
  if (!layout) {
    const i = c.start;
    const L = c.WL[i]! + 0.04;
    const R = -c.WR[i]! - 0.04;
    const P = (o: number, h: number) => {
      const [x, y] = at(c, i, o);
      return cam(x, y, h);
    };
    const beam = [P(L, 0.38), P(R, 0.38), P(R, 0.46), P(L, 0.46)];
    if (visible(beam))
      items.push({
        z: depth(beam) - 0.01,
        draw: () => {
          for (const o of [L, R])
            clipDraw([P(o + 0.015, 0), P(o - 0.015, 0), P(o - 0.015, 0.46), P(o + 0.015, 0.46)], '#2b313b');
          clipDraw(beam, '#1b2027', '#0f141b');
          for (let k = 0; k < 5; k++)
            for (const h of [0.405, 0.435]) {
              const p = P(0.24 - k * 0.12, h);
              if (p.z < NEAR) continue;
              const q = toScreen(p);
              const rr = Math.max(1.5, (F * 0.011) / p.z);
              const on = k < lightsOn;
              g.fillStyle = on ? SCENE.light : SCENE.lightOff;
              if (on) {
                g.shadowColor = SCENE.light;
                g.shadowBlur = rr * 3;
              }
              g.beginPath();
              g.arc(q.u, q.v, rr, 0, Math.PI * 2);
              g.fill();
              g.shadowBlur = 0;
            }
        },
      });
  }
  if (high) {
    for (const p of c.crowd) {
      const h = 0.15 + p.row * 0.12 + 0.12;
      const b = proj(p.x, p.y, h);
      const t = proj(p.x, p.y, h + 0.1);
      if (b && t)
        items.push({
          z: cam(p.x, p.y).f - 0.002,
          draw: () => {
            const s = (b.v - t.v) * 0.8;
            g.fillStyle = p.c;
            g.globalAlpha = 0.55;
            g.beginPath();
            g.ellipse(b.u, b.v - s * 0.3, s * 0.22, s * 0.32, 0, 0, Math.PI * 2);
            g.fill();
            g.globalAlpha = 0.8;
            g.fillStyle = '#c9ced6';
            g.beginPath();
            g.arc(b.u, b.v - s * 0.78, s * 0.15, 0, Math.PI * 2);
            g.fill();
            g.globalAlpha = 1;
          },
        });
    }
    for (const d of c.deco) {
      const hgt = d.k === 'oak' ? d.w * 1.1 : d.k === 'spruce' ? d.w * 2.2 : d.k === 'tires' ? 0.16 : 0.45;
      const b = proj(d.x, d.y);
      const t = proj(d.x, d.y, hgt);
      if (!b || !t) continue;
      items.push({
        z: cam(d.x, d.y).f,
        draw: () => {
          const w = (F * d.w) / b.z;
          const h = b.v - t.v;
          if (d.k === 'oak' || d.k === 'spruce') {
            const im = treeImage(d.k);
            if (!im) return;
            const ww = (h * im.naturalWidth) / im.naturalHeight;
            g.fillStyle = 'rgb(10 20 8 / 35%)';
            g.beginPath();
            g.ellipse(b.u, b.v, ww * 0.4, Math.max(1, ww * 0.06), 0, 0, Math.PI * 2);
            g.fill();
            g.drawImage(im, b.u - ww / 2, t.v, ww, h);
          } else if (d.k === 'tires') tireStack(g, b.u, t.v, b.v, w, 2);
          else {
            g.fillStyle = d.k === 'tent_r' ? '#c95a3a' : '#2d6fb0';
            g.beginPath();
            g.moveTo(b.u - w / 2, b.v);
            g.lineTo(b.u + w / 2, b.v);
            g.lineTo(b.u, t.v);
            g.closePath();
            g.fill();
          }
        },
      });
    }
  }
  const boxes: { o: SimObstacle; u: number; top: number; bot: number; w: number }[] = [];
  for (const o of obstacles) {
    const kind = o.kind ?? 'cone';
    const b = proj(o.x, o.y);
    const height = layout?.obstacle_seeds.find((seed) => seed.id === o.id)?.h ?? OBSTACLE_H[kind];
    const t = proj(o.x, o.y, height);
    if (!b || !t) continue;
    items.push({
      z: cam(o.x, o.y).f - 0.003,
      draw: () => {
        const w = (F * o.w) / b.z;
        const h = b.v - t.v;
        if (kind === 'cone') {
          g.fillStyle = SCENE.cone;
          g.beginPath();
          g.moveTo(b.u - w / 2, b.v);
          g.lineTo(b.u + w / 2, b.v);
          g.lineTo(b.u, t.v);
          g.closePath();
          g.fill();
          g.fillStyle = '#dee2ed';
          g.fillRect(b.u - w * 0.2, b.v - h * 0.55, w * 0.4, h * 0.12);
        } else if (kind === 'box') {
          g.fillStyle = SCENE.box;
          g.fillRect(b.u - w / 2, t.v, w, h);
          g.strokeStyle = '#59606b';
          g.strokeRect(b.u - w / 2, t.v, w, h);
          g.fillStyle = '#c9ced6';
          g.fillRect(b.u - w / 2, t.v + h * 0.42, w, h * 0.12);
        } else tireStack(g, b.u, t.v, b.v, w, 3);
        boxes.push({ o, u: b.u, top: t.v, bot: b.v, w });
      },
    });
  }
  items.sort((p, q) => q.z - p.z);
  for (const it of items) it.draw();
  if (showBoxes) {
    g.font = `600 ${Math.max(12, W / 45)}px "JetBrains Mono", ui-monospace, monospace`;
    for (const bx of boxes) {
      const d = Math.hypot(bx.o.x - pose.x, bx.o.y - pose.y);
      if (d > LOOK_M + 1.5) continue;
      const pad = bx.w * 0.25;
      const x0 = bx.u - bx.w / 2 - pad;
      const y0 = bx.top - pad;
      const near = d < LOOK_M;
      g.strokeStyle = near ? '#4edea3' : '#8b919d';
      g.lineWidth = 2;
      g.strokeRect(x0, y0, bx.w + pad * 2, bx.bot - bx.top + pad * 2);
      const label = `${OBSTACLE_NAME[bx.o.kind ?? 'cone']} ${Math.round(realM(c, d))} m`;
      const tw = g.measureText(label).width + 8;
      const lh = Math.max(16, W / 34);
      g.fillStyle = near ? '#4edea3' : '#59606b';
      g.fillRect(x0, y0 - lh, tw, lh);
      g.fillStyle = '#0f141b';
      g.fillText(label, x0 + 4, y0 - lh * 0.28);
    }
  }
}
