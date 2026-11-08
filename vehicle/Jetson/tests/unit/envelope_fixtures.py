"""S05-b1의 합성 outer JSON fixture. typed payload·peer/admission 성공 증거가 아니다."""

import json


def fields(kind='HELLO', sequence=1, *, payload=None, domain='LIFECYCLE'):
    return {'version': 1, 'service_instance': '00000000-0000-4000-8000-000000000001',
            'channel_epoch': '00000000-0000-4000-8000-000000000002',
            'sender_instance': '00000000-0000-4000-8000-000000000003',
            'message_sequence': str(sequence), 'kind': kind, 'binding_domain': domain,
            'binding_revision': '0', 'request_id': '00000000-0000-4000-8000-000000000004',
            'issued_j_us': '100', 'expires_j_us': '200',
            'payload': {} if payload is None else payload}


def raw(kind='HELLO', sequence=1, *, payload=None, domain='LIFECYCLE'):
    return json.dumps(fields(kind, sequence, payload=payload, domain=domain), sort_keys=True,
                      ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
