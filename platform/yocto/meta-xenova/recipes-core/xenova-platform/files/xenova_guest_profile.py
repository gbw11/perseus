"""검증된 작은 자산 metadata의 통신/신호 선언. 수신·건강·출력 허가를 판단하지 않는다."""
import copy

SIGNALS=('agent_heartbeat','app_heartbeat','telemetry_v2','app_progress')
CHANNELS={'agent','telemetry','drive'}


def validate(value,*,guest_kind=None):
    if (not isinstance(value,dict) or set(value)!={'schema_version','source','channels','signals','apps'}
            or value['schema_version']!='1.0' or not isinstance(value['source'],str)
            or not isinstance(value['channels'],list) or any(type(c) is not str for c in value['channels'])
            or len(set(value['channels']))!=len(value['channels'])
            or any(c not in CHANNELS for c in value['channels'])
            or not isinstance(value['signals'],dict) or set(value['signals'])!=set(SIGNALS)
            or any(v is not None and type(v) is not bool for v in value['signals'].values())
            or not isinstance(value['apps'],dict)):
        raise ValueError('invalid guest communication declaration')
    for app,entry in value['apps'].items():
        if (app not in ('autonomous','manual') or not isinstance(entry,dict)
                or set(entry)!={'model','standby_progress'} or entry['model'] not in ('stateful','stateless',None)
                or entry['standby_progress'] not in ('idle','unknown')):
            raise ValueError('invalid application declaration')
    if ('drive' in value['channels'] and 'telemetry' not in value['channels'] or
            guest_kind=='linux' and set(value['channels'])-{'agent'} or
            guest_kind=='zephyr' and 'agent' in value['channels']):
        raise ValueError('communication declaration has no supported guest adapter')
    return copy.deepcopy(value)


def from_manifest(meta, *, config=''):
    """원 manifest를 바꾸지 않는다. 구형 자산은 확인한 제공 경계만 해석한다."""
    kind='linux' if meta.get('source',{}).get('distribution') in ('debian','ubuntu') else 'zephyr'
    if 'provisioning_profile' in meta:return validate(meta['provisioning_profile'],guest_kind=kind)
    value={'schema_version':'1.0','source':'undeclared','channels':[],
           'signals':dict.fromkeys(SIGNALS),'apps':{}}
    if meta.get('source',{}).get('distribution') in ('debian','ubuntu'):
        agent=meta.get('guest_agent')
        if agent is not None:
            if not isinstance(agent,dict) or not isinstance(agent.get('verification'),dict):
                raise ValueError('invalid Agent installation receipt')
            if type(agent['verification'].get('installed')) is not bool:
                raise ValueError('invalid Agent installation declaration')
            if agent['verification'].get('installed') is True:
                value.update(source='verified-agent-install',channels=['agent'])
                value['signals']['agent_heartbeat']=True
                value['signals']['telemetry_v2']=True
            else:
                value['source']='verified-agent-absent'
                value['signals'].update(agent_heartbeat=False,telemetry_v2=False)
    else:
        app=meta.get('source',{}).get('app')
        if app in ('xenova-starter','xenova-network-starter'):
            value['source']='verified-starter-source';value['signals']['app_heartbeat']=False
        if 'CONFIG_XENOVA_TELEMETRY=y' in config.splitlines():
            value['channels'].append('telemetry');value['signals']['telemetry_v2']=True
        if app in ('xenova-drive','xenova-manual','xenova-autonomous'):
            value['source']='verified-drive-source';value['channels'].append('drive')
            value['signals'].update(app_heartbeat=True,app_progress=True)
            lattice='CONFIG_XENOVA_DRIVE_LATTICE=y' in config.splitlines()
            for name in ('autonomous','manual') if app=='xenova-drive' else (app[7:],):
                value['apps'][name]={'model':('stateful' if name=='autonomous' else 'stateless') if lattice else None,
                                    'standby_progress':'idle' if 'CONFIG_XENOVA_DRIVE_WARM=y' in config.splitlines() else 'unknown'}
    return validate(value)
