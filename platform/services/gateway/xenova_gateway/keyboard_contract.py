"""15장 keyboard-source-1 exact packet. K는 MQTT/Authority/serial 권위를 소유하지 않는다."""
from .ipc_contract import decode as json_decode,uuid,integer
from .operator_contract import depth,decimal
from .authority_journal import canonical

ENDPOINT='/run/xenova-gateway/keyboard.sock'
FIELDS={'version','kind','host_boot_id','gateway_instance_id','source_instance','channel_epoch',
    'sequence','request_id','issued_pi_us','expires_pi_us','payload'}
PAYLOADS={
    'HELLO':{'device_profile','device_identity_sha256','kernel_abi_sha256'},
    'ATTACH':{'expected_generation','expected_owner','device_instance','link_epoch'},
    'GRANT':{'driver_session','input_epoch','grant_revision','generation','profile_sha256','state','reason'},
    'FRAME':{'driver_session','input_epoch','grant_revision','device_instance','link_epoch','source_sample_seq',
        'key_event_seq','t1_pi_us','t2_pi_us','key_mask','last_event_pi_us'},
    'ARM':{'driver_session','input_epoch','grant_revision','source_sample_seq','arm_event_seq'},
    'FENCE':{'driver_session','input_epoch','grant_revision','fence_revision','key_event_seq','reason_mask','reason'},
    'FENCE_ACK':{'driver_session','input_epoch','fence_revision','accepted_pi_us'},
    'STOP_READY':{'driver_session','input_epoch','fence_revision','stop_command_sequence','stationary_sample_sequence'},
    'STATE':{'driver_session','input_epoch','grant_revision','generation','state','reason','reply_to_kind',
        'reply_to_sequence','accepted','accepted_state_seq'},
    'DETACH':{'driver_session','input_epoch','final_fence_revision','reason'}}
REASONS=frozenset(('OK','HELLO_ACCEPTED','ATTACHED','ARMED','DRIVER_BUSY','WRONG_MODE','STOP_REQUIRED',
    'NEUTRAL_REQUIRED','INPUT_EXPIRED','STALE_SAMPLE','SOURCE_MISMATCH','UNSUPPORTED_DEVICE','UNVERIFIED_RADIO',
    'PROTOCOL_ERROR','KEY_CONFLICT','MODIFIER','INPUT_BACKLOG','SYN_DROPPED','DEVICE_LOST','LINK_LOST',
    'GLOBAL_STOP','DETACHED','REVOKED','UNCONFIRMED'))
UUID_FIELDS={'driver_session','input_epoch','device_instance','link_epoch','expected_owner'}
INTEGER_FIELDS={'generation','expected_generation','key_mask','reason_mask'}
COUNTERS={'grant_revision','source_sample_seq','key_event_seq','t1_pi_us','t2_pi_us','last_event_pi_us',
    'arm_event_seq','fence_revision','accepted_pi_us','stop_command_sequence','stationary_sample_sequence',
    'reply_to_sequence','accepted_state_seq','final_fence_revision'}


def decode(raw):
    if len(raw)>2048:raise ValueError('keyboard packet cap')
    value=json_decode(raw);depth(value,6);return validate(value)


def validate(value):
    if (type(value) is not dict or set(value)!=FIELDS or type(value['version']) is not int or value['version']!=1
            or value['kind'] not in PAYLOADS or type(value['payload']) is not dict
            or set(value['payload'])!=PAYLOADS[value['kind']]):raise ValueError('keyboard exact envelope')
    for name in ('source_instance','channel_epoch','request_id'):uuid(value[name])
    for name in ('host_boot_id','gateway_instance_id'):
        if value[name] is not None:uuid(value[name])
        elif value['kind']!='HELLO':raise ValueError('keyboard identity required')
    for name in ('sequence','issued_pi_us','expires_pi_us'):
        if value[name] is None and value['kind']=='HELLO' and name!='sequence':continue
        number=decimal(value[name]);integer(number,minimum=1 if name=='sequence' else 0)
    if value['issued_pi_us'] is not None and not int(value['issued_pi_us'])<int(value['expires_pi_us']):
        raise ValueError('keyboard original expiry')
    for name,item in value['payload'].items():
        if name in UUID_FIELDS:
            if item is not None:uuid(item)
            elif value['kind'] not in ('STATE',):raise ValueError('keyboard source UUID required')
        elif name in INTEGER_FIELDS:
            integer(item,maximum=255 if name=='key_mask' else 15 if name=='reason_mask' else 2**32-1)
        elif name in COUNTERS:
            if item is None and name in ('last_event_pi_us','reply_to_sequence','accepted_state_seq'):continue
            integer(decimal(item))
        elif name.endswith('sha256'):
            if type(item) is not str or len(item)!=64 or any(c not in '0123456789abcdef' for c in item):raise ValueError('keyboard SHA256')
        elif name=='reason':
            if item not in REASONS:raise ValueError('keyboard registered reason')
        elif name=='accepted':
            if item is not None and type(item) is not bool:raise ValueError('keyboard accepted flag')
        elif item is not None and (type(item) is not str or not 1<=len(item)<=64 or not item.isascii()):
            raise ValueError('keyboard bounded identifier')
    if len(canonical(value))>2048:raise ValueError('keyboard packet cap')
    return value
