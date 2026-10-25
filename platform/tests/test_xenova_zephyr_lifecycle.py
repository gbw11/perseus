"""Source snapshot closure; lifecycle/thread acceptance uses the native_sim apps."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class CandidateInputsTest(unittest.TestCase):
    def test_candidate_version_does_not_change_factory_source_lock(self):
        spec=importlib.util.spec_from_file_location('starter_builder',ROOT/'platform/scripts/build-xenova-network-starter.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        import copy,json
        path=ROOT/'platform/guests/zephyr/network-starter.lock.json'
        before=path.read_bytes();locked=json.loads(before);original=copy.deepcopy(locked)
        files={'src/main.c':{'sha256':'a'*64,'bytes':1}}
        candidate=builder.candidate_lock(locked,files,'1.1.1-lifecycle.2')
        self.assertEqual(candidate['app_files'],files)
        self.assertNotEqual(candidate['id'],locked['id'])
        self.assertEqual(locked,original);self.assertEqual(path.read_bytes(),before)
        for version in (locked['version'],'../bad'):
            with self.assertRaises(ValueError):builder.candidate_lock(locked,files,version)
        with self.assertRaises(ValueError):builder.candidate_lock(locked,files)
        self.assertEqual(builder.candidate_lock(locked,locked['app_files']),locked)

    def test_drive_snapshot_runs_its_real_packager_without_repository_imports(self):
        spec=importlib.util.spec_from_file_location('drive_builder',ROOT/'platform/scripts/build-xenova-drive-app.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='xenova-candidate-inputs-') as tmp:
            snapshot=Path(tmp)
            for name in builder.fingerprint('manual'):
                target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,target)
            result=subprocess.run([sys.executable,'-B',str(snapshot/'platform/scripts/package-xenova-firmware.py'),'--help'],
                                  cwd=snapshot,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('--networked',result.stdout)


class SocketLifecycleTest(unittest.TestCase):
    def test_production_transport_ownership_and_one_iovec_deadline(self):
        """Only socket/clock boundaries are replaced; no copied transport logic."""
        with tempfile.TemporaryDirectory(prefix='xenova-socket-lifecycle-') as tmp:
            root=Path(tmp)
            headers={
                'zephyr/kernel.h':'#include <stdint.h>\nint64_t k_uptime_get(void);\n',
                'zephyr/sys/fdtable.h':'#include <fcntl.h>\n#define ZVFS_F_SETFL F_SETFL\n#define ZVFS_O_NONBLOCK O_NONBLOCK\n',
                'zephyr/net/mqtt.h':'#include <stdint.h>\nstruct mqtt_client { struct { void *custom_transport_data; } transport; const void *broker; };\n',
                'zephyr/net/socket.h':r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <netinet/in.h>
#include <poll.h>
#define net_sockaddr_in sockaddr_in
#define net_msghdr msghdr
typedef socklen_t net_socklen_t;
#define NET_AF_INET AF_INET
#define NET_SOCK_STREAM SOCK_STREAM
#define NET_IPPROTO_TCP IPPROTO_TCP
#define ZSOCK_POLLOUT POLLOUT
#define ZSOCK_POLLIN POLLIN
#define ZSOCK_POLLERR POLLERR
#define ZSOCK_POLLHUP POLLHUP
#define ZSOCK_POLLNVAL POLLNVAL
#define ZSOCK_SOL_SOCKET SOL_SOCKET
#define ZSOCK_SO_ERROR SO_ERROR
#define ZSOCK_MSG_DONTWAIT MSG_DONTWAIT
struct zsock_pollfd { int fd;short events,revents; };
int zsock_poll(struct zsock_pollfd *,int,int);
int zsock_socket(int,int,int);
int zsock_fcntl(int,int,int);
int zsock_connect(int,const void *,size_t);
int zsock_getsockopt(int,int,int,void *,net_socklen_t *);
int zsock_send(int,const void *,size_t,int);
int zsock_recv(int,void *,size_t,int);
int zsock_close(int);
'''}
            for name,value in headers.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('#pragma once\n'+value)
            main=root/'main.c'
            main.write_text(r'''
#include <zephyr/net/mqtt.h>
#include <xenova/mqtt_socket.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
static int64_t clock_ms;
static int mode,closes,sockets,sends;
int64_t k_uptime_get(void){return clock_ms;}
int zsock_poll(struct zsock_pollfd *p,int n,int ms){(void)n;clock_ms+=ms;p->revents=p->events;return mode==3?0:1;}
int zsock_socket(int a,int b,int c){(void)a;(void)b;(void)c;sockets++;return 7;}
int zsock_fcntl(int a,int b,int c){(void)a;(void)b;(void)c;if(mode==1){errno=EIO;return -1;}return 0;}
int zsock_connect(int a,const void *p,size_t n){(void)a;(void)p;(void)n;return 0;}
int zsock_getsockopt(int a,int b,int c,void *p,net_socklen_t *n){(void)a;(void)b;(void)c;(void)n;*(int *)p=0;return 0;}
int zsock_send(int f,const void *p,size_t n,int flags){(void)f;(void)p;(void)n;(void)flags;clock_ms+=2;sends++;return 1;}
int zsock_recv(int f,void *p,size_t n,int flags){(void)f;(void)p;(void)n;(void)flags;errno=EAGAIN;return -1;}
int zsock_close(int f){assert(f>=0);closes++;if(mode==2){errno=EINTR;return -1;}return 0;}
int mqtt_client_custom_transport_connect(struct mqtt_client *);
int mqtt_client_custom_transport_disconnect(struct mqtt_client *);
int mqtt_client_custom_transport_write_msg(struct mqtt_client *,const struct net_msghdr *);
int mqtt_client_custom_transport_read(struct mqtt_client *,uint8_t *,uint32_t,bool);
int main(void){
 struct xt_socket s={.fd=-1,.deadline_ms=100};struct mqtt_client m={.transport={&s}};
 mode=1;assert(mqtt_client_custom_transport_connect(&m)==-EIO&&s.fd==7);
 mode=0;assert(!xt_socket_close(&s)&&closes==1&&s.fd==-1);
 assert(!mqtt_client_custom_transport_disconnect(&m)&&closes==1);
 mode=2;s.fd=8;assert(xt_socket_close(&s)==-EINTR&&s.fd==-1&&closes==2);
 assert(xt_socket_close(&s)==-EINTR&&closes==2);
 int before=sockets;assert(mqtt_client_custom_transport_connect(&m)==-EINTR&&sockets==before);
 struct xt_socket other={.fd=9,.deadline_ms=5};m.transport.custom_transport_data=&other;mode=0;
 char bytes[3]={0};struct iovec vec[2]={{bytes,3},{bytes,3}};
 struct net_msghdr msg={.msg_iov=vec,.msg_iovlen=2};
 assert(mqtt_client_custom_transport_write_msg(&m,&msg)==-ETIMEDOUT&&sends==3);
 clock_ms=0;mode=3;other.deadline_ms=7;
 assert(mqtt_client_custom_transport_read(&m,(uint8_t *)bytes,1,true)==-ETIMEDOUT&&clock_ms==7);
 mode=0;assert(!xt_socket_close(&other));assert(closes==3);
 puts("PASS production socket owner, ambiguous close quarantine, vector/read total deadline");
}
''')
            source=ROOT/'platform/guests/zephyr/telemetry/src/socket_transport.c'
            compiler=shutil.which('cc');self.assertIsNotNone(compiler)
            for suffix,flags in (('native',[]),('sanitized',['-fsanitize=address,undefined','-fno-omit-frame-pointer','-no-pie'])):
                binary=root/suffix
                cmd=[compiler,'-std=c11','-Wall','-Wextra','-Werror','-g','-I'+str(root),
                     '-I'+str(source.parent.parent/'include'),str(source),str(main),'-o',str(binary),*flags]
                result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('PASS production',result.stdout)
