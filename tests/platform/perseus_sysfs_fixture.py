"""고정 Linux xen-netback의 부모/버스/드라이버와 bridge brif 링크를 재현한다.

interface.c의 SET_NETDEV_DEV와 xenbus_probe{,_backend}.c의 장치/드라이버 이름에
근거한다. 일반 파일·심볼릭 링크만 만들며 실제 sysfs나 네트워크를 수정하지 않는다.
"""
import os
from pathlib import Path


class XenSysfs:
    def __init__(self, base):
        self.root = Path(base) / 'sys'
        self.net = self.root / 'class/net'
        self.net.mkdir(parents=True)
        self.bridge = self.root / 'devices/virtual/net/xenbr0'
        (self.bridge / 'bridge').mkdir(parents=True)
        (self.bridge / 'brif').mkdir()
        self.link(self.net / 'xenbr0', self.bridge)

    @staticmethod
    def link(link, target):
        link.symlink_to(os.path.relpath(target, link.parent))

    def add_port(self, name, kind='netback'):
        if kind == 'netback':
            bus, driver = 'xen-backend', 'vif'
            device = self.root / 'devices' / name.replace('.', '-').replace('vif', 'vif-', 1)
        else:
            bus, driver = ('xen', 'vif') if kind == 'netfront' else ('platform', 'bcmgenet')
            device = self.root / 'devices/platform' / name
        node = device / 'net' / name
        (node / 'brport').mkdir(parents=True)
        driver_path = self.root / 'bus' / bus / 'drivers' / driver
        driver_path.mkdir(parents=True, exist_ok=True)
        self.link(device / 'subsystem', self.root / 'bus' / bus)
        self.link(device / 'driver', driver_path)
        self.link(node / 'device', device)
        self.link(self.net / name, node)
        self.link(self.bridge / 'brif' / name, node / 'brport')
        return node
