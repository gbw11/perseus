"""`python -m dom0_manager` 로 서비스를 띄운다. 설정은 환경변수(XENOVA_*)에서 읽는다."""

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "dom0_manager.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
