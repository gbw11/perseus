"""보드별 WPA2-Personal 설정과 비밀을 제외한 상태 조회. VM 내부망/NAT는 변경하지 않는다."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
from xenova_runtime import PlatformError, atomic_write, operation_lock
from xenova_catalog import plain

INTERFACE=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}\Z')


def command(args, timeout=15):
    p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
    if p.returncode:
        raise PlatformError('Wi-Fi command failed: '+args[0])
    return p.stdout


def settings(ssid, password, country):
    """SSID는 UTF-8 hex, PSK는 PBKDF2 또는 검증된 64자리 hex로만 기록한다."""
    if not isinstance(ssid,str) or not 1<=len(ssid.encode('utf-8'))<=32 or any(ord(c)<32 for c in ssid):
        raise PlatformError('SSID must be 1..32 UTF-8 bytes without control characters')
    if not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country):
        raise PlatformError('two-letter uppercase regulatory country required')
    if re.fullmatch('[0-9a-fA-F]{64}',password):
        psk=password.lower()
    elif 8<=len(password)<=63 and all(32<=ord(c)<=126 for c in password):
        psk=hashlib.pbkdf2_hmac('sha1',password.encode('ascii'),ssid.encode('utf-8'),4096,32).hex()
    else:
        raise PlatformError('WPA2 PSK must be 8..63 printable ASCII characters or 64 hex digits')
    return ('ctrl_interface=/run/wpa_supplicant\nupdate_config=0\ncountry='+country+
            '\ndisable_scan_offload=1\nnetwork={\n    ssid='+ssid.encode('utf-8').hex()+
            '\n    psk='+psk+'\n    key_mgmt=WPA-PSK\n    proto=RSN\n}\n')


class ActivationError(PlatformError):
    """자격증명/하위 명령 출력을 포함하지 않는 활성화 단계 오류."""
    def __init__(self, action, stage):
        self.activation = {'action': action, 'result': 'failed', 'stage': stage}
        super().__init__('Wi-Fi activation failed during ' + stage + '; saved configuration retained')


class Wifi:
    def __init__(self, root=Path('/'), run=command):
        self.root=Path(root);self.run=run

    def interface(self, name):
        if not isinstance(name,str) or not INTERFACE.fullmatch(name):
            raise PlatformError('invalid wireless interface name')
        path=self.root/'sys/class/net'/name
        if not ((path/'wireless').is_dir() or (path/'uevent').is_file() and 'DEVTYPE=wlan' in (path/'uevent').read_text().splitlines()):
            raise PlatformError('interface is not an existing wireless device')
        if (path/'master').exists() or (path/'master').is_symlink():
            raise PlatformError('wireless device already has a master; no bridge changes performed')

    def configure(self, name, ssid, password, country):
        self.interface(name)
        content=settings(ssid,password,country)
        with operation_lock(self.root/'run/xenova-wifi'):
            directory=self.root/'etc/xenova/wifi'
            current=self.root
            for component in ('etc','xenova'):
                current=current/component
                if not current.exists() and not current.is_symlink():
                    current.mkdir()
                plain(current,directory=True)
            directory.mkdir(mode=0o700,exist_ok=True)
            plain(directory,directory=True);directory.chmod(0o700)
            target=directory/(name+'.conf')
            if target.exists() or target.is_symlink():
                plain(target)
            network=self.root/'etc/systemd/network'
            systemd=self.root/'etc/systemd'
            if not systemd.exists() and not systemd.is_symlink():
                systemd.mkdir()
            plain(systemd,directory=True)
            if not network.exists() and not network.is_symlink():
                network.mkdir()
            plain(network,directory=True)
            path=network/'25-xenova-wifi.network'
            if path.exists() or path.is_symlink():
                plain(path)
            if [p for p in directory.glob('*.conf') if p.name!=target.name]:
                raise PlatformError('another wireless interface is configured; explicit removal required')
            atomic_write(target,content,0o600)
            atomic_write(path,'[Match]\nName='+name+'\nType=wlan\n\n[Link]\nRequiredForOnline=no\n\n[Network]\nDHCP=ipv4\nIPv6AcceptRA=no\n\n[DHCPv4]\nRouteMetric=600\n', 0o644)
            # root 셸의 umask가 0077이어도 비밀 없는 networkd 입력은 읽을 수 있어야 한다.
            path.chmod(0o644)
        return {'configured':True,'interface':name,'credentials':'stored root-only','activation_required':True}

    def activate(self, name):
        """소유한 단일 서비스를 시작/재시작한다. 요청 성공은 AP 인증/IP 성공과 별개다."""
        self.interface(name)
        with operation_lock(self.root / 'run/xenova-wifi'):
            conf = plain(self.root / 'etc/xenova/wifi' / (name + '.conf'))
            if conf.stat().st_mode & 0o077:
                raise PlatformError('Wi-Fi configuration must be root-only')
            unit = 'xenova-wifi@' + name + '.service'
            action, stage = None, 'state-query'
            try:
                state = self.run(['systemctl', 'show', '--property=ActiveState', '--value', unit]).strip()
                if state not in ('active', 'inactive', 'failed'):
                    raise ActivationError(None, 'service-state')
                action = 'restart' if state == 'active' else 'start'
                stage = 'enable'
                self.run(['systemctl', 'enable', unit])
                stage = 'supplicant'
                self.run(['systemctl', action, unit])
                stage = 'network-reload'
                self.run(['networkctl', 'reload'])
                stage = 'network-reconfigure'
                self.run(['networkctl', 'reconfigure', name])
            except ActivationError:
                raise
            except (PlatformError, OSError, subprocess.SubprocessError) as exc:
                raise ActivationError(action, stage) from exc
            result = self.status(name)
            result['activation'] = {'action': action, 'result': 'requested', 'stage': 'complete'}
            return result

    def status(self,name):
        self.interface(name)
        configured=(self.root/'etc/xenova/wifi'/(name+'.conf')).is_file()
        result={'interface':name,'configured':configured,'authentication':'not-configured',
                'ipv4':[],'internal_subnet_overlap':False,'errors':[]}
        if configured:
            try:
                values=dict(line.split('=',1) for line in self.run(['wpa_cli','-i',name,'status']).splitlines() if '=' in line)
                result['authentication']=values.get('wpa_state','unknown')
            except (PlatformError,subprocess.SubprocessError,OSError):
                result['authentication']='unavailable';result['errors'].append('supplicant status unavailable')
        try:
            rows=json.loads(self.run(['ip','-j','-4','address','show','dev',name]))
            for row in rows:
                for address in row.get('addr_info',[]):
                    local,prefix=address['local'],address['prefixlen']
                    result['ipv4'].append(local+'/'+str(prefix))
                    network=ipaddress.ip_network(local+'/'+str(prefix),strict=False)
                    result['internal_subnet_overlap'] |= network.overlaps(ipaddress.ip_network('192.168.77.0/24'))
        except (KeyError,TypeError,ValueError,PlatformError,subprocess.SubprocessError,OSError):
            result['errors'].append('address query failed')
        if result['internal_subnet_overlap']:
            result['errors'].append('management network overlaps guest subnet; network is not changed')
        return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['configure','status','activate'])
    p.add_argument('--interface',default='wlan0')
    p.add_argument('--country',default='KR')
    args=p.parse_args(argv);wifi=Wifi()
    if args.action=='configure':
        if not sys.stdin.isatty():
            raise PlatformError('configure requires an interactive terminal; no password arguments accepted')
        ssid=input('Wi-Fi SSID: ')
        password=input('Wi-Fi PSK: ')
        result=wifi.configure(args.interface,ssid,password,args.country)
    elif args.action=='activate':
        try:
            result=wifi.activate(args.interface)
        except ActivationError as exc:
            print(json.dumps({'interface':args.interface, 'activation':exc.activation,
                              'error':str(exc)},indent=2))
            return 1
    else:
        result=wifi.status(args.interface)
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except (PlatformError,OSError,subprocess.SubprocessError):
        print('[xenova-wifi] operation failed; check interface, root-only configuration and service status',file=sys.stderr)
        sys.exit(1)
