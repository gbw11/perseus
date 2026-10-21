/**
 * mock 데이터. RC카 시나리오의 VM 3대(자율주행·수동주행 Zephyr, 인터랙션 Debian) + 템플릿.
 * 모양은 api/types.ts (VM 관리 API 계약 v1 §2.2 §2.3 §3.3) 그대로.
 */
import type { Template, Vm } from '../api/types';

export const MOCK_USER = { user_id: '00000000-0000-4000-8000-000000000001', username: 'admin' };
/** 로그인 mock 비밀번호. 실제 계정이 아니다 */
export const MOCK_PASSWORD = 'mock-password';

export const TEMPLATES: Template[] = [
  {
    template_id: 'zephyr-drive',
    version: '1.0.0',
    display_name: 'Zephyr 주행 앱',
    min_memory_mib: 64,
    guest_kind: 'zephyr',
  },
  {
    template_id: 'debian-12',
    version: '1.0.0',
    display_name: 'Debian 12',
    min_memory_mib: 256,
    guest_kind: 'linux',
  },
  {
    template_id: 'ubuntu-24.04',
    version: '1.0.0',
    display_name: 'Ubuntu 24.04',
    min_memory_mib: 384,
    guest_kind: 'linux',
  },
];

export function initialVms(): Vm[] {
  return [
    {
      vm_id: '11111111-1111-4111-8111-111111111111',
      name: 'autonomous',
      guest_kind: 'zephyr',
      state: 'running',
      agent_status: 'UNKNOWN', // Zephyr 는 에이전트가 없다 (§2.2)
      ipv4: '10.0.0.11',
      template_id: 'zephyr-drive',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 64,
    },
    {
      vm_id: '22222222-2222-4222-8222-222222222222',
      name: 'manual',
      guest_kind: 'zephyr',
      state: 'running',
      agent_status: 'UNKNOWN',
      ipv4: '10.0.0.12',
      template_id: 'zephyr-drive',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 64,
    },
    {
      vm_id: '33333333-3333-4333-8333-333333333333',
      name: 'interaction',
      guest_kind: 'linux',
      state: 'running',
      agent_status: 'ONLINE',
      ipv4: '10.0.0.13',
      template_id: 'debian-12',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 384,
    },
  ];
}
