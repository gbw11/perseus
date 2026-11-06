#!/usr/bin/env node
/**
 * 노트북에서 HDMI 모니터 화면 미리보기: 가짜 dom0 수집기(8082) + vm-screens 개발 서버(5174).
 * 열 주소: http://localhost:5174/display/monitor-1 (계기판), /display/monitor-2 (인포테인먼트) — 실제 모니터는 1920×1080.
 * 시나리오: http://127.0.0.1:8082/scenario/<cycle|cruise|obstacle|safe-stop|auto-ready|failover-done|manual|feed-down>
 */
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const app = join(dirname(fileURLToPath(import.meta.url)), '..');
const children = [
  spawn(process.execPath, [join(app, 'scripts', 'fake-display-feed.mjs')], { cwd: app, stdio: 'inherit' }),
  // HDMI 화면은 VITE_TELEMETRY 를 비운 display 모드 (dom0 수집기 경로)
  spawn('npx', ['vite', '--port', '5174', '--strictPort'], {
    cwd: app,
    stdio: 'inherit',
    shell: true,
    env: { ...process.env, VITE_TELEMETRY: '', VITE_DISPLAY_FEED_ORIGIN: 'http://127.0.0.1:8082' },
  }),
];
const stop = () => children.forEach((child) => child.kill());
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
children.forEach((child) => child.on('exit', stop));
