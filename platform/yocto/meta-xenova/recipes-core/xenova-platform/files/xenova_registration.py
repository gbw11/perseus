"""등록 metadata의 순수 projection. 실행 관측·자격·외부 송신은 하지 않는다."""
import ipaddress


def registration(record):
    """현재 저장 구성과 생성 출처를 반환한다. 관측값·최신 catalog로 보정하지 않는다."""
    import xenova_vm as vm
    vm.validate_identity(record)
    # 구형 factory는 template_id 없이 당시 source template만 보존했다.
    template=record.get('template_id',record.get('template'))
    name=record.get('display_name',record['name'])
    version=record.get('template_version')
    if (not isinstance(template,str) or not template or not isinstance(name,str) or not name
            or version is not None and (not isinstance(version,str) or not version)):
        raise ValueError('registered template/name metadata invalid')
    for field in ('vcpus','memory_mib'):
        if type(record.get(field)) is not int or record[field]<=0:
            raise ValueError('registered resource metadata invalid')
    address=record.get('ipv4')
    if address is not None:
        if not isinstance(address,str) or str(ipaddress.IPv4Address(address))!=address:
            raise ValueError('registered IPv4 metadata invalid')
    return {'vm_id':record['uuid'],'name':name,'guest_kind':vm.guest_kind(record),
            'template_id':template,'template_version':version,'vcpus':record['vcpus'],
            'memory_mib':record['memory_mib'],'assigned_ipv4':address}
