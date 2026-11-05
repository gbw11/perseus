/**
 * mock 데이터. RC카 시나리오의 VM 3대(자율주행·수동주행 Zephyr, 네비게이션 Debian) + 템플릿.
 * 모양은 api/types.ts (VM 관리 API 계약 v1 §2.2 §2.3 §3.3) 그대로.
 */
import type { Template, Vm } from '../api/types';

export const MOCK_USER = {
  user_id: '00000000-0000-4000-8000-000000000001',
  username: 'admin',
  nickname: '관리자',
};
/** 로그인 mock 비밀번호. 실제 계정이 아니다 */
export const MOCK_PASSWORD = '1234';

// VM 관리 API 계약 v1 §2.3 예시와 같은 템플릿 3종 (Ubuntu 24.04 · Debian 13 · Zephyr)
export const TEMPLATES: Template[] = [
  {
    template_id: 'ubuntu-24.04-arm64',
    version: '1.0.0',
    display_name: 'Ubuntu 24.04',
    min_memory_mib: 512,
    guest_kind: 'linux',
  },
  {
    template_id: 'debian-13-arm64',
    version: '1.0.0',
    display_name: 'Debian 13',
    min_memory_mib: 256,
    guest_kind: 'linux',
  },
  {
    template_id: 'zephyr-xenvm-starter-arm64',
    version: '1.0.0',
    display_name: 'Zephyr Starter 1.0.0',
    min_memory_mib: 16,
    guest_kind: 'zephyr',
  },
];

export const VM_IDS = {
  autonomous: '11111111-1111-4111-8111-111111111111',
  manual: '22222222-2222-4222-8222-222222222222',
  interaction: '33333333-3333-4333-8333-333333333333',
} as const;

export function initialVms(): Vm[] {
  return [
    {
      vm_id: VM_IDS.autonomous,
      name: 'autonomous',
      guest_kind: 'zephyr',
      state: 'running',
      agent_status: 'UNKNOWN', // Zephyr 는 에이전트가 없다 (§2.2)
      ipv4: null,
      assigned_ipv4: null,
      template_id: 'zephyr-xenvm-starter-arm64',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 16,
      role: 'primary_drive',
    },
    {
      vm_id: VM_IDS.manual,
      name: 'manual',
      guest_kind: 'zephyr',
      state: 'running',
      agent_status: 'UNKNOWN',
      ipv4: null,
      assigned_ipv4: null,
      template_id: 'zephyr-xenvm-starter-arm64',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 16,
      role: 'backup_drive',
    },
    {
      vm_id: VM_IDS.interaction,
      name: 'interaction',
      guest_kind: 'linux',
      state: 'running',
      agent_status: 'ONLINE',
      ipv4: '192.168.77.13',
      assigned_ipv4: '192.168.77.13',
      template_id: 'debian-13-arm64',
      template_version: '1.0.0',
      vcpus: 1,
      memory_mib: 512,
      role: 'interaction',
    },
  ];
}
