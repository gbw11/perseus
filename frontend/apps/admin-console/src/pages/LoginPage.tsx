import { Button, Card } from '@xenova/ui';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useLogin } from '../api/queries';
import { useSession } from '../auth';
import styles from './pages.module.css';

/** 기본형 — 인증 흐름 확인용. 화면 완성은 S15P31E110-53 (로그인 화면) */
export function LoginPage() {
  const session = useSession();
  const login = useLogin();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? '/vms';
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
      <Card title="Xenova 관리 콘솔 로그인" style={{ width: 360 }}>
        <form className={styles.form} onSubmit={submit}>
          <label>
            아이디
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
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
              required
            />
          </label>
          {error && (
            <p className={styles.error} role="alert">
              {error.message}
            </p>
          )}
          <Button type="submit" disabled={login.isPending}>
            {login.isPending ? '확인 중…' : '로그인'}
          </Button>
        </form>
      </Card>
    </div>
  );
}
