import logo from '@xenova/ui/assets/xenova-logo.png';
import { Button } from '@xenova/ui';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useLogin } from '../api/queries';
import { useSession } from '../auth';
import styles from './pages.module.css';

const ERROR_TEXT: Record<string, string> = {
  INVALID_CREDENTIALS: '아이디 또는 비밀번호가 맞지 않습니다',
  LOGIN_RATE_LIMITED: '로그인 시도가 많습니다. 잠시 후 다시 시도하세요',
};

/**
 * W-01 로그인 (화면 정의서 W-01, UI-01) — 초안. 화면 완성은 S15P31E110-53 (정송하) 가 이어받는다.
 * 참고 시안: stitch_untitled_project_init/xenova_1.
 */
export function LoginPage() {
  const session = useSession();
  const login = useLogin();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? '/';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  if (session) return <Navigate to={from} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate({ username, password }, { onSuccess: () => navigate(from, { replace: true }) });
  };
  const error = login.error instanceof ApiError ? login.error : null;

  return (
    <div className={styles.center}>
      <section
        className={styles.pageHead}
        style={{ width: 420, flexDirection: 'column', alignItems: 'stretch', padding: 40 }}
      >
        <div style={{ textAlign: 'center' }}>
          <img src={logo} alt="Xenova" style={{ height: 44, width: 'auto', margin: '0 auto 12px' }} />
          <h1 style={{ fontSize: 'var(--x-text-lg)' }}>관리 콘솔 로그인</h1>
        </div>
        <form className={styles.form} onSubmit={submit}>
          <label>
            아이디
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="아이디 입력"
              required
            />
          </label>
          <label>
            비밀번호
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="비밀번호 입력"
              required
            />
          </label>
          {error && (
            <p className={styles.error} role="alert">
              {ERROR_TEXT[error.code] ?? error.message}
            </p>
          )}
          <Button type="submit" size="lg" disabled={login.isPending}>
            {login.isPending ? '확인 중…' : '로그인'}
          </Button>
        </form>
      </section>
    </div>
  );
}
