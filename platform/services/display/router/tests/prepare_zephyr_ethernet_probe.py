"""Create a lab-only manual heartbeat/Ethernet build; never modifies sender sources.

Generated firmware disables all drive.command publication and input/control subscriptions.
It reuses the existing manual heartbeat loop and MQTT encoder/client; XenStore is shared
with netfront. Output contains a short-lived test credential: keep outside Git.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[5]


def replace_once(text, old, new):
    if text.count(old)!=1:raise ValueError('sender source changed; adapter must be reviewed')
    return text.replace(old,new,1)


def prepare(output, port, password, main_priority=0):
    if main_priority not in (0,10):raise ValueError('only recorded lab comparison profiles are supported')
    manual=ROOT/'platform/apps/manual/zephyr'
    original=(manual/'src/main.c').read_text()
    text=original
    start=text.index('static void resolve_vm_id(void)')
    end=text.index('static void latest_payload(',start)
    text=text[:start]+'''static int resolve_vm_id(void)
{
 char path[64];
 int rc=xsr_read("vm",path,sizeof(path),3000);
 return rc?rc:manual_vm_id_from_xenstore(path,vm_id);
}

'''+text[end:]
    text=replace_once(text,'#include "xm_zclient.h"','''#include "xm_zclient.h"
#include <xenova/runtime.h>
#include <xenova/netfront.h>
#include <xenova/shutdown.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/net/dhcpv4.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_ip.h>
#include <stdio.h>
static atomic_t probe_stopping,probe_stopped;
static int probe_quiesce(const char *request,int64_t deadline,void *context) {
 ARG_UNUSED(request);ARG_UNUSED(context);atomic_set(&probe_stopping,1);
 while(!atomic_get(&probe_stopped)) {
  if(k_uptime_get()>=deadline)return -ETIMEDOUT;
  k_sleep(K_MSEC(10));
 }
 return 0;
}
''')
    text=replace_once(text,'\tresolve_vm_id();','''
 if(xshutdown_start(probe_quiesce,NULL)!=0)return -EIO;
 int network_rc=xnet_start();
 if(network_rc!=0){char error[16];snprintf(error,sizeof(error),"%d",network_rc);xsr_write("data/display253/error",error,1000);return network_rc;}
 /* Lab-only static IP: existing DHCP reservations cover Debian only. */
 struct net_if *probe_iface=net_if_get_default();
 struct in_addr probe_ip,probe_mask,probe_gateway;
 net_dhcpv4_stop(probe_iface);
 net_addr_pton(AF_INET,"192.168.77.32",&probe_ip);
 net_addr_pton(AF_INET,"255.255.255.0",&probe_mask);
 net_addr_pton(AF_INET,"192.168.77.1",&probe_gateway);
 if(!net_if_ipv4_addr_add(probe_iface,&probe_ip,NET_ADDR_MANUAL,0))return -ENOMEM;
 net_if_ipv4_set_netmask_by_addr(probe_iface,&probe_ip,&probe_mask);
 net_if_ipv4_set_gw(probe_iface,&probe_gateway);
 xsr_write("data/display253/stage","network-ready",1000);
 int uuid_rc=resolve_vm_id();
 if(uuid_rc!=0){char error[24];snprintf(error,sizeof(error),"uuid=%d",uuid_rc);xsr_write("data/display253/error",error,1000);return uuid_rc;}
 xsr_write("data/display253/stage","uuid-ready",1000);
 printk("DISPLAY253 heartbeat-only trial: no drive commands or control subscriptions\\n");
''')
    for line in ('\txm_zclient_subscribe(&client, control, 1);', '\txm_zclient_subscribe(&client, input, 0);'):
        text=replace_once(text,line,'\t/* Display probe: no control/input subscriptions. */')
    line='\t\t\txm_zclient_publish(&client, events, "drive.command", json); /* 끊겨 있으면 버린다 (QoS 0) */'
    text=replace_once(text,line,'\t\t\t/* Display probe: drive.command publication disabled. */')
    text=replace_once(text,'\tfor (;;) {','\twhile (!atomic_get(&probe_stopping)) {')
    text=replace_once(text,'\tint64_t start = k_uptime_get();', '\txsr_write("data/display253/stage","mqtt-initialized",1000);\n\tint64_t start = k_uptime_get();')
    text=replace_once(text,'\t\t\t\txm_zclient_publish(&client, events, "app.heartbeat", json);', '''
                int report_rc=xm_zclient_publish(&client, events, "app.heartbeat", json);
                char diagnostic[96];
                snprintf(diagnostic,sizeof(diagnostic),"connected=%d socket=%d publish=%d sent=%u",client.connected,client.socket_open,report_rc,client.published);
                xsr_write("data/display253/mqtt",diagnostic,1000);
                struct xnf_stats net_stats;
                if(xnet_stats(&net_stats)==0) {
                 snprintf(diagnostic,sizeof(diagnostic),"tx=%u rx=%u tx_drop=%u rx_drop=%u",net_stats.tx,net_stats.rx,net_stats.tx_drop,net_stats.rx_drop);
                 xsr_write("data/display253/network",diagnostic,1000);
                }
''')
    text=replace_once(text,'\treturn 0;\n}', '\tatomic_set(&probe_stopped,1);\n\treturn 0;\n}')
    assert 'xm_zclient_publish(&client, events, "drive.command"' not in text
    assert 'xm_zclient_subscribe(&client,' not in text
    output.mkdir(parents=True,exist_ok=False)
    (output/'main.c').write_text(text)
    mqtt_source=ROOT/'platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.c'
    mqtt_original=mqtt_source.read_text()
    mqtt_diagnostic='#include <xenova/runtime.h>\n'+replace_once(mqtt_original,
        '\t\t\t\tLOG_DBG("MQTT 연결 실패 %d", rc);',
        '\t\t\t\tchar diagnostic[24];snprintf(diagnostic,sizeof(diagnostic),"%d",rc);xsr_write("data/display253/connect-error",diagnostic,1000);')
    (output/'mqtt_diagnostic.c').write_text(mqtt_diagnostic)
    modules=[ROOT/'platform/apps/common/xenova-mqtt-c',ROOT/'platform/guests/zephyr/xen-store',ROOT/'platform/guests/zephyr/xen-runtime']
    cmake='cmake_minimum_required(VERSION 3.20.5)\nlist(APPEND ZEPHYR_EXTRA_MODULES\n'+''.join(f' "{p.as_posix()}"\n' for p in modules)+')\nfind_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(display253_heartbeat_trial)\n'
    cmake+=f'target_sources(app PRIVATE main.c "{(manual/"src/manual.c").as_posix()}")\ntarget_include_directories(app PRIVATE "{(manual/"src").as_posix()}")\n'
    cmake+='get_target_property(_xm_sources xenova_mqtt SOURCES)\nlist(FILTER _xm_sources EXCLUDE REGEX "xm_zclient[.]c$")\nset_property(TARGET xenova_mqtt PROPERTY SOURCES ${_xm_sources})\ntarget_sources(xenova_mqtt PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/mqtt_diagnostic.c)\n'
    (output/'CMakeLists.txt').write_text(cmake)
    (output/'Kconfig').write_text(f'mainmenu "Display253 lab heartbeat trial"\nsource "{(manual/"Kconfig").as_posix()}"\n')
    network=ROOT/'platform/guests/zephyr/network-starter-app'
    config=(network/'prj.conf').read_text().replace('CONFIG_NET_TCP=n','CONFIG_NET_TCP=y')
    config+='\nCONFIG_NET_SOCKETS=y\nCONFIG_NET_SOCKETS_POLL_MAX=4\nCONFIG_MQTT_LIB=y\nCONFIG_XENOVA_MQTT=y\n'
    # Optional lab comparison only; lowering main priority did not establish live recovery.
    # Keep the previous default; this is not a vehicle scheduling decision.
    config+=f'CONFIG_MAIN_THREAD_PRIORITY={main_priority}\n'
    config+=f'CONFIG_XENOVA_MANUAL_BROKER_HOST="192.168.77.1"\nCONFIG_XENOVA_MANUAL_BROKER_PORT={port}\nCONFIG_XENOVA_MANUAL_BROKER_USERNAME="manual"\nCONFIG_XENOVA_MANUAL_BROKER_PASSWORD="{password}"\n'
    (output/'prj.conf').write_text(config);(output/'prj.conf').chmod(0o600)
    (output/'app.overlay').write_text((network/'app.overlay').read_text())
    receipt={'scope':'QEMU VM2 candidate only; manual heartbeat/Ethernet; no drive commands or subscriptions',
             'manual_main_sha256':hashlib.sha256(original.encode()).hexdigest(),
             'adapted_main_sha256':hashlib.sha256(text.encode()).hexdigest(),
             'mqtt_original_sha256':hashlib.sha256(mqtt_original.encode()).hexdigest(),
             'mqtt_diagnostic_sha256':hashlib.sha256(mqtt_diagnostic.encode()).hexdigest(),
             'lab_main_thread_priority':main_priority,
             'broker_port':port,'source_root':str(ROOT)}
    (output/'source-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--credential-file',type=Path,required=True)
    parser.add_argument('--main-priority',type=int,choices=(0,10),default=0,help='lab comparison profile; neither proves VIF live recovery')
    args=parser.parse_args()
    credential=json.loads(args.credential_file.read_text())['manual']
    if not 1024<=args.port<=65535 or not credential.isalnum():parser.error('invalid test port or credential')
    prepare(args.output,args.port,credential,args.main_priority)
