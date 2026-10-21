"""실제 Linux PTY/자식 프로세스로 검증. Xen/보드/추가 테스트 패키지 불필요."""
import asyncio
import os
import signal
import sys
import unittest

from dom0_manager.console import ConsoleError, ConsoleExited, ConsoleSession


ECHO = """
import os
os.write(1, b'READY')
while True:
    data = os.read(0, 4096)
    if b'\\x1d' in data:
        break
    os.write(1, data)
"""


@unittest.skipUnless(sys.platform == 'linux', 'Linux PTY tests')
class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    def session(self, code=ECHO):
        return ConsoleSession('guest-1', xl_command=(sys.executable, '-u', '-c', code),
                              close_timeout=0.2)

    async def until(self, session, marker):
        output = b''
        async with asyncio.timeout(3):
            while marker not in output:
                output += await session.read()
        return output

    async def test_raw_binary_io_and_normal_detach(self):
        async with self.session() as session:
            self.assertEqual(await self.until(session, b'READY'), b'READY')
            # Ctrl+C, ANSI, UTF-8 and CR must pass without local echo/conversion.
            payload = b'\x03\x1b[A\r' + '한글'.encode()
            await session.write(payload)
            self.assertEqual(await self.until(session, payload), payload)
            pid = session.pid
        self.assertEqual(session.returncode, 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        await session.close()

    async def test_idle_timeout_and_cancellation_allow_next_read(self):
        async with self.session() as session:
            await self.until(session, b'READY')
            with self.assertRaises(TimeoutError):
                await session.read(timeout=0.02)
            reading = asyncio.create_task(session.read())
            await asyncio.sleep(0.02)
            reading.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await reading
            await session.write(b'ok')
            self.assertEqual(await self.until(session, b'ok'), b'ok')

    async def test_close_wakes_pending_read(self):
        async with self.session() as session:
            await self.until(session, b'READY')
            reading = asyncio.create_task(session.read())
            await asyncio.sleep(0.02)
            await session.close()
            with self.assertRaises(ConsoleError):
                await reading

    async def test_child_failure_preserves_output_and_exit_code(self):
        async with self.session("import os; os.write(2,b'no such guest'); raise SystemExit(7)") as session:
            output = b''
            with self.assertRaises(ConsoleExited) as error:
                while True:
                    output += await session.read(timeout=3)
            self.assertIn(b'no such guest', output)
            self.assertEqual(error.exception.returncode, 7)

    async def test_stubborn_child_cleanup_even_when_close_cancelled(self):
        code = "import signal,time,os; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.write(1,b'READY'); time.sleep(60)"
        session = self.session(code)
        await session.open()
        try:
            await self.until(session, b'READY')
            cleanup = asyncio.create_task(session.close())
            await asyncio.sleep(0.03)
            cleanup.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cleanup
            self.assertEqual(session.returncode, -signal.SIGKILL)
            with self.assertRaises(ProcessLookupError):
                os.kill(session.pid, 0)
        finally:
            await session.close()

    async def test_launch_failure_and_closed_operations(self):
        session = ConsoleSession('guest', xl_command=('/no/such/xl',))
        with self.assertRaises(ConsoleError):
            await session.open()
        await session.close()
        with self.assertRaises(ConsoleError):
            await session.write(b'x')
        with self.assertRaises(ConsoleError):
            await session.read()
        with self.assertRaises(ConsoleError):
            await session.open()

    async def test_backpressure_does_not_block_loop(self):
        async with self.session() as session:
            await self.until(session, b'READY')
            payload = b'z' * 65536
            writing = asyncio.create_task(session.write(payload, timeout=3))
            output = b''
            async with asyncio.timeout(3):
                while len(output) < len(payload):
                    output += await session.read()
            await writing
            self.assertEqual(output, payload)

    async def test_repeated_sessions_do_not_leak_descriptors(self):
        baseline = len(os.listdir('/proc/self/fd'))
        for _ in range(12):
            async with self.session() as session:
                await self.until(session, b'READY')
        self.assertEqual(len(os.listdir('/proc/self/fd')), baseline)


class DomainValidationTests(unittest.TestCase):
    def test_invalid_domain_rejected(self):
        for domain in ('0', '00', 'Domain-0', 'dom0', '-1', '--help', 'vm;id', 'vm\n1', ''):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                ConsoleSession(domain)


if __name__ == '__main__':
    unittest.main()
