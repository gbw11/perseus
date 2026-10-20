"""Xenova dom0-manager.

Spring 관리 서버가 호출하는 내부 HTTP API(127.0.0.1:8081)를 제공하고, 드라이버(Mock/Xen)를 통해
Xen 도메인을 조회·제어한다. 계약은 docs/contracts/dom0-manager-openapi.yaml 과
docs/contracts/vm-api-contract-v1.md 를 따른다.
"""

__version__ = "0.1.0"
