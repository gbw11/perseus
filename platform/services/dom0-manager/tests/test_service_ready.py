"""준비 신호가 실제 listen 성공 뒤에만 나오는지 확인한다."""
import asyncio
import os
import socket
from unittest.mock import patch

import pytest
import uvicorn
from fastapi import FastAPI

from dom0_manager.__main__ import ReadyServer


def test_ready_datagram_follows_successful_listen_and_is_not_sent_after_bind_failure(tmp_path):
    endpoint=str(tmp_path/'notify.sock')
    async def exercise():
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as receiver:
            receiver.bind(endpoint);receiver.setblocking(False)
            server=ReadyServer(uvicorn.Config(FastAPI(),host='127.0.0.1',port=0,log_level='critical',lifespan='on'))
            server.config.load();server.lifespan=server.config.lifespan_class(server.config)
            with patch.dict(os.environ,{'NOTIFY_SOCKET':endpoint}):await server.startup()
            assert server.started
            assert receiver.recv(64)==b'READY=1'
            port=server.servers[0].sockets[0].getsockname()[1]
            # 이미 점유한 port로 시작하면 READY를 만들지 않는다.
            failed=ReadyServer(uvicorn.Config(FastAPI(),host='127.0.0.1',port=port,log_level='critical',lifespan='on'))
            failed.config.load();failed.lifespan=failed.config.lifespan_class(failed.config)
            with patch.dict(os.environ,{'NOTIFY_SOCKET':endpoint}),pytest.raises(SystemExit):await failed.startup()
            assert not failed.started
            with pytest.raises(BlockingIOError):receiver.recv(64)
            await server.shutdown()
    asyncio.run(exercise())
