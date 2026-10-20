import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// vitest globals 를 쓰지 않으므로 렌더 정리를 직접 건다
afterEach(cleanup);
