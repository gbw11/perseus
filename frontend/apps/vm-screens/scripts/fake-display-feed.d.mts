/** 시험에서 쓰는 가짜 수집기 내보내기 (fake-display-feed.mjs) */
export const SCENARIOS: string[];
export const IDS: { primary: string; backup: string; interaction: string };
export function build(now: number): unknown;
export function setScenario(name: string): void;
export function setTripForTest(mapDistance: number, now?: number): void;
