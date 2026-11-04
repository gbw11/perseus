"""Synthetic state for local browser probes only; never imported by runtime."""
def presentation(phase):
    source={'status':'fresh','age_ms':100}
    roles=['primary_drive','backup_drive','interaction']
    ids=[f'00000000-0000-4000-8000-00000000000{i}' for i in (1,2,3)]
    vms=[dict(vm_id=vm,role=role,run_id=f'run-{i}',xen_state='running',app_state='unknown',
              agent_supported=i==2,os=dict(expected='debian' if i==2 else 'zephyr',observed=None,current_run_verified=False),
              sources={key:source if key=='xen' else {'status':'unknown','age_ms':None} for key in ['xen','app','agent','inventory']},
              screen_source={'kind':'status','handle':None}) for i,(vm,role) in enumerate(zip(ids,roles))]
    uncertain=phase=='uncertain'
    active=phase=='applied'
    return dict(schema_version=1,type='display.presentation',policy_epoch='LOCAL-SYNTHETIC',revision=1,render_revision='local',
                outputs=[dict(output_id=f'monitor-{i+1}',view='overview' if i==0 else 'detail',screen='instrument' if i==0 else 'infotainment',
                              selection='pinned',vm_id=None if i==0 else ids[0]) for i in (0,1)],
                decisions={vm:dict(kind='app_waiting',label='VM 실행 중 · 앱 상태 미확인',tone='warning') for vm in ids},
                state=dict(source_epoch='LOCAL-SYNTHETIC',sequence=1,generated_at='2026-11-02T00:00:00Z',mqtt_connected=False,vms=vms,
                           drive=dict(active_vm_id=ids[0] if active else None,mode='unknown' if uncertain else 'AUTO',ai_status='unknown',source=source,
                                      authority=dict(selected_vm_id=ids[0],selected_mode='AUTO',generation=4,phase='APPLIED' if active else 'DISARMED',
                                                     output_permitted=active,storage_uncertain=uncertain))))
