import { Button, Card } from '@xenova/ui';
import { useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useSignup } from '../api/queries';
import { useSession } from '../auth';
import styles from './pages.module.css';

export function SignupPage() {
  const session = useSession();
  const signup = useSignup();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [nickname, setNickname] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  if (session) return <Navigate to="/vms" replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (password !== passwordConfirm) {
      setLocalError('비밀번호가 일치하지 않습니다.');
      return;
    }
    setLocalError(null);
    signup.mutate(
      { username, nickname, password },
      { onSuccess: () => navigate('/login', { replace: true }) },
    );
  };
  const apiError = signup.error instanceof ApiError ? signup.error.message : null;

  return (
    <div className={styles.center}>
      <Card title="Xenova 회원가입" style={{ width: 380 }}>
        <form className={styles.form} onSubmit={submit}>
          <label>
            아이디
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              minLength={4}
              maxLength={32}
              required
            />
          </label>
          <label>
            닉네임
            <input
              value={nickname}
              onChange={(event) => setNickname(event.target.value)}
              autoComplete="nickname"
              maxLength={50}
              required
            />
          </label>
          <label>
            비밀번호
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              maxLength={64}
              required
            />
          </label>
          <label>
            비밀번호 확인
            <input
              type="password"
              value={passwordConfirm}
              onChange={(event) => setPasswordConfirm(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              maxLength={64}
              required
            />
          </label>
          {(localError || apiError) && (
            <p className={styles.error} role="alert">
              {localError || apiError}
            </p>
          )}
          <Button type="submit" disabled={signup.isPending}>
            {signup.isPending ? '가입 중…' : '회원가입'}
          </Button>
          <p className={styles.dim}>
            이미 계정이 있나요? <Link to="/login">로그인</Link>
          </p>
        </form>
      </Card>
    </div>
  );
}
