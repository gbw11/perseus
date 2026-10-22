import { at, STAND_SIDE, surfaceAt, wallOf, type Circuit } from './circuit';
import { SCENE, tireTop, treeImage } from './paint';
import type { SimObstacle, SimPose } from './types';

export interface TopView {
  s: number;
  ox: number;
  oy: number;
}

export interface TopScene {
  pose: SimPose | null;
  obstacles: SimObstacle[];
  trail: [number, number][];
  carColor: string;
  /** 차 몸 크기 (sim.world.car) */
  car: { length_m: number; width_m: number };
}

/** 캔버스 크기에 트랙 전체를 맞추는 배율·원점 */
export function fitTop(g: Circuit, W: number, H: number): TopView {
  const { x0, x1, y0, y1 } = g.box;
  const s = Math.min(W / (x1 - x0), H / (y1 - y0));
  return { s, ox: (W - s * (x1 - x0)) / 2 - s * x0, oy: (H - s * (y1 - y0)) / 2 + s * y1 };
}

const w2s = (v: TopView, x: number, y: number): [number, number] => [v.ox + x * v.s, v.oy - y * v.s];

/** 잔디 길 색: 출발 직선(벽이 연석 바로 뒤)은 아스팔트 앞치마, 코너 바깥은 흙, 나머지는 깎은 줄무늬 잔디 */
export function runoffColor(g: Circuit, i: number, sd: number): string {
  if (wallOf(g, i, sd) - g.road < 0.08) return SCENE.apron;
  if (surfaceAt(g, i, sd * (g.road + 0.01)) === 'dirt') return SCENE.dirt;
  return Math.floor(i / 3) % 2 ? SCENE.grass : SCENE.grass2;
}

const bgCache = new WeakMap<Circuit, { W: number; H: number; canvas: HTMLCanvasElement }>();

/** 움직이지 않는 것(풀밭·트랙·벽·관중석·나무)은 한 번 그려 둔다 */
function background(g: Circuit, W: number, H: number, v: TopView): HTMLCanvasElement {
  const hit = bgCache.get(g);
  if (hit && hit.W === W && hit.H === H) return hit.canvas;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const c = canvas.getContext('2d');
  if (!c) return canvas;
  const poly = (pts: [number, number][]) => {
    c.beginPath();
    pts.forEach((p, k) => {
      const [u, vv] = w2s(v, p[0], p[1]);
      if (k) c.lineTo(u, vv);
      else c.moveTo(u, vv);
    });
    c.closePath();
  };
  const path = (off: number) => {
    c.beginPath();
    for (let i = 0; i <= g.N; i++) {
      const [x, y] = at(g, i % g.N, off);
      const [u, vv] = w2s(v, x, y);
      if (i) c.lineTo(u, vv);
      else c.moveTo(u, vv);
    }
  };
  // 바깥 풀밭 + 잔무늬 (고정 난수)
  c.fillStyle = SCENE.field;
  c.fillRect(0, 0, W, H);
  let q = 5;
  const rr = () => (q = (q * 16807) % 2147483647) / 2147483647;
  c.globalAlpha = 0.35;
  for (let k = 0; k < 2600; k++) {
    c.fillStyle = rr() < 0.5 ? SCENE.field2 : '#2d4625';
    const x = rr() * W;
    const y = rr() * H;
    const r = 2 + rr() * 9;
    c.beginPath();
    c.ellipse(x, y, r * 1.6, r, rr() * 3, 0, Math.PI * 2);
    c.fill();
  }
  c.globalAlpha = 1;
  c.lineJoin = 'round';
  // 관중석
  for (let i = 0; i < g.N; i++) {
    if (!g.standRows[i]) continue;
    const j = (i + 1) % g.N;
    const D = (r: number) => (g.WR[r]! + 0.3 + g.standRows[i]! * 0.16) * STAND_SIDE;
    poly([
      at(g, i, (g.WR[i]! + 0.2) * STAND_SIDE),
      at(g, j, (g.WR[j]! + 0.2) * STAND_SIDE),
      at(g, j, D(j)),
      at(g, i, D(i)),
    ]);
    c.fillStyle = SCENE.stand;
    c.strokeStyle = SCENE.stand;
    c.fill();
    c.stroke();
  }
  c.globalAlpha = 0.85;
  for (const p of g.crowd) {
    const [u, vv] = w2s(v, p.x, p.y);
    c.fillStyle = p.c;
    c.beginPath();
    c.arc(u, vv, Math.max(1, 0.035 * v.s), 0, Math.PI * 2);
    c.fill();
  }
  c.globalAlpha = 1;
  // 잔디 길 → 아스팔트
  for (let i = 0; i < g.N; i++)
    for (const sd of [1, -1]) {
      const j = (i + 1) % g.N;
      poly([
        at(g, i, sd * g.road),
        at(g, j, sd * g.road),
        at(g, j, sd * wallOf(g, j, sd)),
        at(g, i, sd * wallOf(g, i, sd)),
      ]);
      const col = runoffColor(g, i, sd);
      c.fillStyle = col;
      c.strokeStyle = col;
      c.lineWidth = 1;
      c.fill();
      c.stroke();
    }
  c.lineCap = 'round';
  path(0);
  c.strokeStyle = SCENE.asphalt;
  c.lineWidth = g.road * 2 * v.s;
  c.stroke();
  // 연석: 코너만 빨강/흰
  c.lineCap = 'butt';
  for (const sd of [1, -1])
    for (let i = 0; i < g.N; i++) {
      if (!g.corner[i]) continue;
      const off = sd * (g.laneW / 2 + (g.road - g.laneW / 2) / 2);
      c.strokeStyle = i % 2 ? SCENE.curbB : SCENE.curbA;
      c.lineWidth = (g.road - g.laneW / 2) * v.s;
      c.beginPath();
      c.moveTo(...w2s(v, ...at(g, i, off)));
      c.lineTo(...w2s(v, ...at(g, (i + 1) % g.N, off)));
      c.stroke();
    }
  c.lineCap = 'round';
  path(0);
  c.strokeStyle = SCENE.asphalt;
  c.lineWidth = g.laneW * v.s;
  c.stroke();
  for (const sd of [1, -1]) {
    path((sd * g.laneW) / 2);
    c.strokeStyle = SCENE.line;
    c.lineWidth = Math.max(1, 0.015 * v.s);
    c.stroke();
  }
  path(0);
  c.strokeStyle = SCENE.dash;
  c.globalAlpha = 0.55;
  c.lineWidth = Math.max(1, 0.018 * v.s);
  c.setLineDash([0.25 * v.s, 0.25 * v.s]);
  c.stroke();
  c.setLineDash([]);
  c.globalAlpha = 1;
  // 벽 (코너는 빨강/흰 띠)
  c.lineCap = 'butt';
  for (const sd of [1, -1])
    for (let i = 0; i < g.N; i++) {
      const j = (i + 1) % g.N;
      c.strokeStyle = g.corner[i] && Math.floor(i / 2) % 2 ? SCENE.curbA : SCENE.wallTop;
      c.lineWidth = Math.max(2, 0.05 * v.s);
      c.beginPath();
      c.moveTo(...w2s(v, ...at(g, i, sd * wallOf(g, i, sd))));
      c.lineTo(...w2s(v, ...at(g, j, sd * wallOf(g, j, sd))));
      c.stroke();
    }
  // 출발선 체크무늬
  {
    const i = g.start;
    const sq = g.laneW / 6;
    for (let r = 0; r < 6; r++)
      for (let k = 0; k < 2; k++) {
        c.fillStyle = (r + k) % 2 ? '#0f141b' : '#dee2ed';
        const along = (k - 0.5) * sq;
        const off = -g.laneW / 2 + (r + 0.5) * sq;
        const x = g.C[i]![0] + g.nor[i]![0] * off + g.tan[i]![0] * along;
        const y = g.C[i]![1] + g.nor[i]![1] * off + g.tan[i]![1] * along;
        const [u, vv] = w2s(v, x, y);
        c.fillRect(u - (sq * v.s) / 2, vv - (sq * v.s) / 2, sq * v.s, sq * v.s);
      }
  }
  // 장식
  for (const d of g.deco) {
    const [u, vv] = w2s(v, d.x, d.y);
    if (d.k === 'oak' || d.k === 'spruce') {
      const r = (d.w * v.s) / 2;
      c.fillStyle = 'rgb(10 20 8 / 45%)';
      c.beginPath();
      c.ellipse(u + r * 0.35, vv + r * 0.3, r, r * 0.9, 0, 0, Math.PI * 2);
      c.fill();
      const im = treeImage(d.k === 'oak' ? 'oak_top' : 'spruce_top');
      if (im) {
        c.save();
        c.translate(u, vv);
        c.rotate(d.a);
        c.drawImage(im, -r, -r, r * 2, r * 2);
        c.restore();
      }
    } else if (d.k === 'tires') {
      const r = Math.max(2.5, (d.w * v.s) / 4);
      for (const dx of [-1, 1]) tireTop(c, u + dx * r, vv, r);
    } else {
      // 텐트: 위에서 본 네 쪽 지붕 (흰색·천 색 번갈아)
      const r = (d.w * v.s) / 3;
      c.save();
      c.translate(u, vv);
      c.rotate(-d.a);
      for (let k = 0; k < 4; k++) {
        c.fillStyle = k % 2 ? '#f1f1f1' : d.k === 'tent_r' ? '#d0603a' : '#2d7fc4';
        c.beginPath();
        c.moveTo(0, 0);
        const a0 = (k * Math.PI) / 2 - Math.PI / 4;
        c.lineTo(Math.cos(a0) * r * 1.414, Math.sin(a0) * r * 1.414);
        c.lineTo(Math.cos(a0 + Math.PI / 2) * r * 1.414, Math.sin(a0 + Math.PI / 2) * r * 1.414);
        c.closePath();
        c.fill();
      }
      c.restore();
    }
  }
  // 아직 안 읽힌 나무 사진이 있으면 다음에 다시 그린다
  const ready = treeImage('oak_top') && treeImage('spruce_top');
  if (ready) bgCache.set(g, { W, H, canvas });
  return canvas;
}

/** 탑뷰 한 장 — 트랙·장애물·지나온 길·차 (차는 판정과 같은 실제 크기) */
export function drawTop(ctx: CanvasRenderingContext2D, W: number, H: number, g: Circuit, scene: TopScene) {
  const v = fitTop(g, W, H);
  ctx.drawImage(background(g, W, H, v), 0, 0);
  const { pose, obstacles, trail, carColor, car } = scene;
  if (trail.length > 1) {
    ctx.beginPath();
    trail.forEach(([x, y], k) => {
      const [u, vv] = w2s(v, x, y);
      if (k) ctx.lineTo(u, vv);
      else ctx.moveTo(u, vv);
    });
    ctx.strokeStyle = carColor;
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = Math.max(2.5, 0.035 * v.s);
    ctx.lineJoin = 'round';
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  for (const o of obstacles) {
    const [u, vv] = w2s(v, o.x, o.y);
    const r = Math.max(4, (o.w * v.s) / 2);
    const i = nearestIdx(g, o.x, o.y);
    ctx.save();
    ctx.translate(u, vv);
    ctx.rotate(-Math.atan2(g.tan[i]![1], g.tan[i]![0]));
    if ((o.kind ?? 'cone') === 'cone') {
      ctx.fillStyle = SCENE.cone;
      ctx.beginPath();
      ctx.moveTo(r, 0);
      ctx.lineTo(-r * 0.8, r * 0.9);
      ctx.lineTo(-r * 0.8, -r * 0.9);
      ctx.closePath();
      ctx.fill();
    } else if (o.kind === 'box') {
      ctx.fillStyle = SCENE.box;
      ctx.fillRect(-r * 0.8, -r, r * 1.6, r * 2);
      ctx.strokeStyle = '#dee2ed';
      ctx.strokeRect(-r * 0.8, -r, r * 1.6, r * 2);
    } else for (const dy of [-0.5, 0.5]) tireTop(ctx, 0, dy * r, r * 0.55);
    ctx.restore();
  }
  if (!pose) return;
  const [u, vv] = w2s(v, pose.x, pose.y);
  const ring = Math.max(14, 0.32 * v.s) * (1 + 0.12 * Math.sin(performance.now() / 250));
  ctx.beginPath();
  ctx.arc(u, vv, ring, 0, Math.PI * 2);
  ctx.strokeStyle = carColor;
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.save();
  ctx.translate(u, vv);
  ctx.rotate(-pose.yaw);
  // 좁은 칸에서도 보이게 최소 크기 (넓으면 판정과 같은 실제 크기)
  const L = Math.max(car.length_m * v.s, 18);
  const Wd = Math.max(car.width_m * v.s, 9);
  ctx.fillStyle = carColor;
  ctx.beginPath();
  ctx.moveTo(L / 2, Wd * 0.25);
  ctx.lineTo(L / 2, -Wd * 0.25);
  ctx.lineTo(L * 0.3, -Wd / 2);
  ctx.lineTo(-L / 2, -Wd / 2);
  ctx.lineTo(-L / 2, Wd / 2);
  ctx.lineTo(L * 0.3, Wd / 2);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = '#0f141b';
  ctx.globalAlpha = 0.5;
  ctx.fillRect(L * 0.05, -Wd * 0.3, L * 0.18, Wd * 0.6);
  ctx.globalAlpha = 1;
  ctx.restore();
}

function nearestIdx(g: Circuit, x: number, y: number) {
  let best = 0;
  let bd = Infinity;
  for (let i = 0; i < g.N; i++) {
    const d = (g.C[i]![0] - x) ** 2 + (g.C[i]![1] - y) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return best;
}
