/* Run on Windows after building vm-screens. Uses only the locally created test application. */
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/SSAFY/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const repo = path.resolve(__dirname, '../../../../..');
const wslRoot = '/mnt/' + repo[0].toLowerCase() + repo.slice(2).replaceAll('\\', '/');
const output = process.env.DISPLAY_PROBE_OUTPUT || path.resolve(repo, '../outputs/display-step3');
fs.mkdirSync(output, { recursive: true });
const token = randomBytes(32).toString('hex');
const prefix = ['-d', 'Ubuntu', '-u', 'root', '--'];
const ssh = ['ssh', '-F', '/home/ssafy/xenova-qemu-28/keys/ssh_config', '-o', 'BatchMode=yes', 'xenova-qemu-dom0', 'xl list -l'];
function domains() {
  return JSON.parse(execFileSync('wsl', [...prefix, ...ssh], { encoding: 'utf8' }))
    .filter(r => r.domid !== 0).map(r => [r.config.c_info.uuid, r.domid]).sort();
}
const before = domains();
const server = spawn('wsl', [...prefix, 'env',
  `PYTHONPATH=${wslRoot}/platform/services/display/router:${wslRoot}/platform/services/dom0-manager:${wslRoot}/platform/services/vm-agent/mqtt-client`,
  '/tmp/mo04-step3-venv/bin/python', '-u', '-B', `${wslRoot}/platform/services/display/router/tests/preview_server.py`],
  { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
server.stderr.pipe(fs.createWriteStream(path.join(output, 'server.log')));
server.stdin.write(JSON.stringify({ token }) + '\n');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
(async () => {
  const line = await Promise.race([
    new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: server.stdout });
      lines.once('line', resolve);
      server.once('exit', code => reject(new Error(`preview exited ${code}`)));
    }), sleep(30000).then(() => { throw new Error('preview startup timeout'); }),
  ]);
  const port = JSON.parse(line).port;
  const address = execFileSync('wsl', [...prefix, 'hostname', '-I'], { encoding: 'utf8' }).trim().split(/\s+/)[0];
  const baseURL = `http://${address}:${port}`;
  browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, baseURL });
  const headers = { Authorization: `Bearer ${token}` };
  const cases = [];
  const errors = [];
  const session = await context.request.post('/api/v1/display/session', { headers });
  assert.equal(session.status(), 204);
  const p1 = await context.newPage(), p2 = await context.newPage();
  for (const p of [p1, p2]) p.on('pageerror', error => errors.push(error.message));
  await p1.goto('/display/monitor-1'); await p2.goto('/display/monitor-2');
  await p1.getByText('VM 실행 중 · 앱 상태 미확인', { exact: true }).first().waitFor();
  await p2.getByTestId('card-VM1').waitFor();
  assert.equal(await p1.locator('[data-role]').count(), 3);
  assert.equal(await p1.getByRole('button').count(), 0);
  assert.equal(await context.request.post('/api/v1/display/selection', { data: {} }).then(r => r.status()), 401);
  async function presentation() { return context.request.get('/api/v1/display/presentation').then(r => r.json()); }
  for (let i = 0; i < 40; i++) {
    const p = await presentation();
    if (p.outputs.every(o => o.renderer_ack?.stage === 'dom_rendered')) break;
    if (i === 39) throw new Error('renderer acknowledgement missing');
    await sleep(200);
  }
  cases.push('real_qemu_xen_three_vm_overview_and_default_primary_detail');
  cases.push('both_dom_render_acknowledgements_and_read_only_session');
  await p1.screenshot({ path: path.join(output, 'qemu-overview.png'), fullPage: true });
  await p2.screenshot({ path: path.join(output, 'qemu-detail.png'), fullPage: true });
  const live = await presentation();
  async function scenario(name) {
    const response = await context.request.post(`/test/scenario/${name}`, { headers });
    assert.equal(response.status(), 200);
    await sleep(1300);
  }
  async function select(selection, vm_id = null) {
    const current = await presentation();
    const response = await context.request.post('/api/v1/display/selection', {
      headers, data: { policy_epoch: current.policy_epoch, revision: current.revision, selection, vm_id },
    });
    assert.equal(response.status(), 200);
    await sleep(1300);
  }
  await scenario('manual');
  await p1.getByText('MANUAL', { exact: true }).waitFor();
  assert.ok((await p1.getByTestId('card-VM1').innerText()).includes('활성 구동'));
  assert.ok(!(await p1.getByTestId('card-VM2').innerText()).includes('활성 구동'));
  cases.push('fixture_manual_mode_keeps_primary_active');
  await scenario('crash');
  await p2.getByText('VM 장애', { exact: true }).waitFor();
  assert.equal(await p2.getByTestId('card-VM1').count(), 1);
  cases.push('fixture_crash_does_not_invent_failover');
  await scenario('stale');
  await p1.getByTestId('card-VM1').getByText('VM 상태 수신 지연 / 연결 끊김', { exact: true }).waitFor();
  cases.push('fixture_stale_xen_hides_normal_status');
  await select('follow_active');
  await scenario('backup');
  await p2.getByTestId('card-VM2').waitFor();
  cases.push('fixture_confirmed_backup_switch_only_in_follow_mode');
  await select('pinned', live.state.vms.find(v => v.role === 'interaction').vm_id);
  await p2.getByText('Debian GNU/Linux', { exact: true }).waitFor();
  cases.push('explicit_debian_selection_renders_debian_face');
  await scenario('live');
  await p1.route('**/api/v1/display/presentation', route => route.abort());
  await p1.getByRole('alert').waitFor({ timeout: 7000 });
  assert.equal(await p1.getByText('활성 구동', { exact: true }).count(), 0);
  cases.push('browser_feed_failure_marks_last_values_and_removes_live_claims');
  await p1.unroute('**/api/v1/display/presentation');
  await p1.getByTestId('monitor-1').and(p1.locator('[data-connected="true"]')).waitFor();
  cases.push('browser_reconnect_recovers_snapshot');
  await p1.setViewportSize({ width: 1280, height: 720 });
  await sleep(300);
  assert.ok(await p1.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await p1.evaluate(() => document.documentElement.scrollHeight <= innerHeight));
  await p2.setViewportSize({ width: 1280, height: 720 });
  await sleep(300);
  await p2.screenshot({ path: path.join(output, 'qemu-detail-720p.png'), fullPage: true });
  assert.ok(await p2.evaluate(() => document.documentElement.scrollHeight <= innerHeight));
  await p1.screenshot({ path: path.join(output, 'qemu-overview-720p.png'), fullPage: true });
  await p2.screenshot({ path: path.join(output, 'qemu-detail-720p.png'), fullPage: true });
  cases.push('720p_both_outputs_fit_without_scroll');
  assert.deepEqual(errors, []);
  const after = domains(); assert.deepEqual(after, before);
  cases.push('no_vm_domain_changes_and_no_browser_errors');
  fs.writeFileSync(path.join(output, 'evidence.json'), JSON.stringify({ passed: true, cases, before, after,
    scope: 'real QEMU Xen observed via SSH; drive/fault transitions are synthetic feed fixtures; no physical HDMI',
    completed_at: new Date().toISOString() }, null, 2) + '\n');
  console.log(JSON.stringify({ passed: true, cases, output }));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  await browser?.close(); server.stdin.end();
  const timer = setTimeout(() => server.kill(), 8000); timer.unref();
});
