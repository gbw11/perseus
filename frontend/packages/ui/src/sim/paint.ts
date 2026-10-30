import { TREE_IMAGES, type TreeKey } from './treeImages';
import type { ObstacleKind } from './types';

/** 장면 색 — 실제 서킷처럼 (화면 테마와 무관하게 늘 같은 색) */
export const SCENE = {
  asphalt: '#3b424d',
  apron: '#343a44',
  line: '#8b919d',
  dash: '#c9ced6',
  curbA: '#ef4444',
  curbB: '#dee2ed',
  wall: '#59606b',
  wallTop: '#8b919d',
  stand: '#30353d',
  cone: '#f59e0b',
  box: '#8b919d',
  grass: '#4f7a3a',
  grass2: '#5a8743',
  dirt: '#9c8662',
  field: '#34502a',
  field2: '#3a5a2f',
  skyTop: '#3d7cc9',
  skyLow: '#a8cbe8',
  hill: '#6f8fa3',
  hill2: '#58764f',
  tire: '#0b0d10',
  tireEdge: '#3a3f46',
  tireHole: '#1f242b',
  light: '#ff2a2a',
  lightOff: '#30353d',
} as const;

/** 장애물 종류마다 높이 (m) — 폭은 sim.world 의 w */
export const OBSTACLE_H: Record<ObstacleKind, number> = { cone: 0.2, box: 0.22, tires: 0.16 };
export const OBSTACLE_NAME: Record<ObstacleKind, string> = { cone: '콘', box: '박스', tires: '타이어' };

const images = new Map<TreeKey, HTMLImageElement>();
/** 나무 사진 (처음 부를 때 읽는다. 아직 안 읽혔으면 null — 그 프레임은 건너뛴다) */
export function treeImage(k: TreeKey): HTMLImageElement | null {
  if (typeof Image === 'undefined') return null;
  let im = images.get(k);
  if (!im) {
    im = new Image();
    im.src = TREE_IMAGES[k].src;
    images.set(k, im);
  }
  return im.complete && im.naturalWidth > 0 ? im : null;
}

/** 위에서 본 타이어 한 개: 검은 고무 + 가운데 휠 구멍 */
export function tireTop(g: CanvasRenderingContext2D, u: number, v: number, r: number) {
  g.beginPath();
  g.arc(u, v, r, 0, Math.PI * 2);
  g.fillStyle = SCENE.tire;
  g.fill();
  g.strokeStyle = SCENE.tireEdge;
  g.lineWidth = 1;
  g.stroke();
  g.beginPath();
  g.arc(u, v, r * 0.45, 0, Math.PI * 2);
  g.fillStyle = SCENE.tireHole;
  g.fill();
}

/** 옆에서 본 타이어 더미: n 단 */
export function tireStack(
  g: CanvasRenderingContext2D,
  u: number,
  top: number,
  bot: number,
  w: number,
  n: number,
) {
  const h = (bot - top) / n;
  for (let q = 0; q < n; q++) {
    const y = bot - h * (q + 1);
    g.fillStyle = SCENE.tire;
    g.fillRect(u - w / 2, y, w, h);
    g.fillStyle = SCENE.tireEdge;
    g.fillRect(u - w / 2, y, w, Math.max(1, h * 0.12));
    g.fillStyle = SCENE.tireHole;
    g.fillRect(u - w * 0.18, y + h * 0.3, w * 0.36, h * 0.4);
  }
}
