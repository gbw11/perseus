import base64,getpass,hashlib,json,socket
import paramiko

PIN = 'xuqfEeu0Ilfz3NvCd1ffCP74KMUIOmEEp7kDGxGeYPw'

def execute(transport, code, timeout=90):
    channel=transport.open_session(timeout=10)
    channel.settimeout(timeout)
    channel.exec_command('python3 -')
    channel.sendall(code.encode())
    channel.shutdown_write()
    out=channel.makefile('rb').read().decode()
    err=channel.makefile_stderr('rb').read().decode()
    status=channel.recv_exit_status()
    if status:raise RuntimeError(f'remote command failed ({status}): {err[:1500]}')
    return json.loads(out)

def connect(password=None):
    transport=paramiko.Transport(socket.create_connection(('192.168.30.96',22),timeout=10))
    transport.start_client(timeout=10)
    pin=base64.b64encode(hashlib.sha256(transport.get_remote_server_key().asbytes()).digest()).decode().rstrip('=')
    if pin!=PIN:
        transport.close()
        raise RuntimeError('SSH host key differs from previously verified board; no authentication attempted')
    secret=getpass.getpass('Board SSH password (not saved): ') if password is None else password
    transport.auth_password('root',secret)
    return transport
