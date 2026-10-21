import { Button, Card, HEALTH_LABEL, Modal, StatusBadge, useToast, type Health } from '@xenova/ui';
import { useState } from 'react';
import styles from './pages.module.css';

const HEALTHS = Object.keys(HEALTH_LABEL) as Health[];

/** 공통 컴포넌트 예시 (S15P31E110-51 완료 조건). 새 컴포넌트를 만들면 여기에 같이 올린다 */
export function ShowcasePage() {
  const [open, setOpen] = useState(false);
  const toast = useToast();

  return (
    <div className={styles.grid}>
      <Card title="Button">
        <div className={styles.row}>
          <Button>primary</Button>
          <Button variant="secondary">secondary</Button>
          <Button variant="danger">danger</Button>
          <Button variant="ghost">ghost</Button>
          <Button disabled>disabled</Button>
        </div>
        <div className={styles.row}>
          <Button size="sm">sm</Button>
          <Button size="md">md</Button>
          <Button size="lg">lg</Button>
        </div>
      </Card>

      <Card title="StatusBadge — RC카 시나리오 §4.3 표시 상태">
        {(['sm', 'md', 'lg'] as const).map((size) => (
          <div key={size} className={styles.row}>
            {HEALTHS.map((health) => (
              <StatusBadge key={health} health={health} size={size} />
            ))}
          </div>
        ))}
      </Card>

      <Card
        title="Card — 앱 역할색 강조"
        accent="var(--x-app-autonomous)"
        actions={<Button size="sm">동작</Button>}
      >
        accent 로 왼쪽 테두리 색을 준다. 자율주행 <code>--x-app-autonomous</code>, 수동주행{' '}
        <code>--x-app-manual</code>, 네비게이션 <code>--x-app-interaction</code>.
      </Card>

      <Card title="색 토큰">
        <div className={styles.swatches}>
          {[
            'ok',
            'fault',
            'recovering',
            'stopped',
            'app-autonomous',
            'app-manual',
            'app-interaction',
            'guest-debian',
            'guest-ubuntu',
            'guest-zephyr',
          ].map((name) => (
            <span key={name} className={styles.swatch}>
              <i style={{ background: `var(--x-${name})` }} />
              {name}
            </span>
          ))}
        </div>
      </Card>

      <Card title="Modal · Toast">
        <div className={styles.row}>
          <Button onClick={() => setOpen(true)}>모달 열기</Button>
          {HEALTHS.map((health) => (
            <Button
              key={health}
              variant="secondary"
              onClick={() => toast.show(`${HEALTH_LABEL[health]} 알림`, health)}
            >
              토스트: {HEALTH_LABEL[health]}
            </Button>
          ))}
        </div>
        <Modal
          open={open}
          title="VM 정지"
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setOpen(false)}>
                취소
              </Button>
              <Button variant="danger" onClick={() => setOpen(false)}>
                정지
              </Button>
            </>
          }
        >
          확인용 모달. Esc 나 바깥 클릭으로도 닫힌다.
        </Modal>
      </Card>
    </div>
  );
}
