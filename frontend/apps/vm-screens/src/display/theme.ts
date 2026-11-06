import { useEffect, useState } from 'react';

export type DisplayTheme = 'light' | 'dark';

/** 낮(07~19시)에는 밝게, 밤에는 어둡게. 양산차 계기판의 자동 주간·야간 전환과 같다 */
export function themeAt(date: Date): DisplayTheme {
  const hour = date.getHours();
  return hour >= 7 && hour < 19 ? 'light' : 'dark';
}

/**
 * 화면 테마. 주소에 ?theme=light | dark 가 있으면 고정, 없거나 auto 면 시각에 따라 바뀐다 (1분마다 확인).
 * 키오스크 주소에 ?theme= 를 붙여 시연장 밝기에 맞출 수 있다.
 */
export function useDisplayTheme(): DisplayTheme {
  const fixed = new URLSearchParams(window.location.search).get('theme');
  const [auto, setAuto] = useState<DisplayTheme>(() => themeAt(new Date()));
  useEffect(() => {
    if (fixed === 'light' || fixed === 'dark') return;
    const timer = window.setInterval(() => setAuto(themeAt(new Date())), 60_000);
    return () => window.clearInterval(timer);
  }, [fixed]);
  return fixed === 'light' || fixed === 'dark' ? fixed : auto;
}
