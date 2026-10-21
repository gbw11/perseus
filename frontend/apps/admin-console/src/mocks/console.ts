/**
 * 웹 콘솔 mock (VITE_MOCK) — WS /ws/v1/vms/{vm_id}/console 을 흉내 낸다 (웹 터미널 계약 browser-console-v1).
 *   클라이언트 → {"type":"auth"} → 서버 {"type":"connected","vm_id"} → 그다음부터는 바이너리만 오간다.
 *   클라이언트 {"type":"disconnect"} → 서버가 1000 으로 닫는다.
 * Linux VM 은 몇 가지 명령에 답하는 가짜 셸, Zephyr VM 은 셸 없이 부팅 로그와 1초 주기 로그만 낸다
 * (실제 Zephyr Starter 와 같다). 실제 게스트에 붙는 것은 백엔드 중계(platform -248) 쪽이다.
 */
import { ws } from 'msw';
import { initialVms, VM_IDS } from './data';

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const bytes = (text: string) => encoder.encode(text.replace(/\n/g, '\r\n')).buffer as ArrayBuffer;

const consoleLink = ws.link('*/ws/v1/vms/:vmId/console');

function shellReply(name: string, line: string, startedAt: number): string {
  const [command = '', ...args] = line.trim().split(/\s+/);
  const up = Math.floor((Date.now() - startedAt) / 1000) + 3600;
  switch (command) {
    case '':
      return '';
    case 'help':
      return '명령: help hostname uname uptime free ps systemctl date whoami clear exit\n';
    case 'hostname':
      return `${name}\n`;
    case 'whoami':
      return 'xenova\n';
    case 'uname':
      return args.includes('-a')
        ? `Linux ${name} 6.12.0-xen-arm64 #1 SMP Debian 6.12 aarch64 GNU/Linux\n`
        : 'Linux\n';
    case 'uptime':
      return ` ${new Date().toTimeString().slice(0, 8)} up ${Math.floor(up / 3600)}:${String(Math.floor((up % 3600) / 60)).padStart(2, '0')},  1 user,  load average: 0.08, 0.05, 0.01\n`;
    case 'free':
      return [
        '               total        used        free      shared  buff/cache   available',
        'Mem:             491         142         201           1         148         336',
        'Swap:              0           0           0',
        '',
      ].join('\n');
    case 'ps':
      return [
        '    PID TTY          TIME CMD',
        '      1 ?        00:00:01 systemd',
        '    212 ?        00:00:04 xenova-agent',
        '    245 ?        00:00:02 message-board',
        '    301 hvc0     00:00:00 bash',
        '',
      ].join('\n');
    case 'systemctl':
      return [
        '● message-board.service - Xenova 인터랙션 앱',
        '     Loaded: loaded (/etc/systemd/system/message-board.service; enabled)',
        '     Active: active (running)',
        '',
      ].join('\n');
    case 'date':
      return `${new Date().toString()}\n`;
    case 'clear':
      return '\x1b[2J\x1b[H';
    default:
      return `-bash: ${command}: command not found\n`;
  }
}

export const consoleHandlers = [
  consoleLink.addEventListener('connection', ({ client, params }) => {
    const vmId = String(params.vmId);
    const vm = initialVms().find((v) => v.vm_id === vmId);
    let ready = false;
    let line = '';
    let timer: ReturnType<typeof setInterval> | undefined;
    const startedAt = Date.now();
    const name = vm?.name ?? vmId;
    const prompt = `xenova@${name}:~$ `;
    const out = (text: string) => client.send(bytes(text));

    client.addEventListener('close', () => clearInterval(timer));
    client.addEventListener('message', (event) => {
      const data = event.data;
      if (typeof data === 'string') {
        const frame = JSON.parse(data) as { type?: string };
        if (frame.type === 'auth' && !ready) {
          if (!vm) return client.close(4404, 'vm not found');
          ready = true;
          client.send(JSON.stringify({ type: 'connected', vm_id: vmId }));
          if (vm.guest_kind === 'zephyr') {
            out(`*** Booting Zephyr OS build v4.1.0 ***\n[00:00:00.010] xen: domU ${name} 시작\n`);
            out(
              `[00:00:00.120] vchan: dom0 중계 연결\n[00:00:00.130] app: ${vmId === VM_IDS.autonomous ? 'autonomous' : 'manual'} 50 Hz 명령 시작\n`,
            );
            let tick = 1;
            timer = setInterval(
              () => out(`[00:00:${String(tick++ % 60).padStart(2, '0')}.000] app.heartbeat\n`),
              1000,
            );
          } else {
            out(`Debian GNU/Linux 13 ${name} hvc0\n\n(mock 콘솔 — 'help' 로 명령 목록)\n`);
          }
        } else if (frame.type === 'disconnect') {
          client.close(1000, 'disconnect');
        }
        return;
      }
      if (!ready || vm?.guest_kind === 'zephyr') return; // Zephyr Starter 는 셸이 없다
      const input = decoder.decode(data instanceof ArrayBuffer ? data : (data as ArrayBufferView));
      for (const ch of input) {
        if (ch === '\r') {
          const command = line;
          line = '';
          out('\n');
          if (command.trim() === 'exit') {
            out('logout\n');
            client.close(1000, 'exit');
            return;
          }
          out(shellReply(name, command, startedAt) + prompt);
        } else if (ch === '\x7f') {
          if (line) {
            line = line.slice(0, -1);
            out('\b \b');
          }
        } else if (ch === '\x03') {
          line = '';
          out(`^C\n${prompt}`);
        } else if (ch >= ' ') {
          line += ch;
          out(ch);
        }
      }
    });
  }),
];
