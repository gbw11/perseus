"""The operator tool submits a contextual request, not an application ACK."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

def test_verified_resume_reuses_current_control_and_closes(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'tools/send_resume.py'
    spec=importlib.util.spec_from_file_location('drive_resume_tool',path)
    tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
    calls=[]
    class Client:
        def __init__(self,*a,**kw):calls.append(('client',kw))
        def subscribe(self,*a,**kw):calls.append(('subscribe',a))
        def start(self,**kw):return True
        def latest(self,_):return SimpleNamespace(payload={'generation':42},envelope={'message_id':'control-id'})
        def publish(self,*a,**kw):calls.append(('publish',a,kw))
        def stop(self):calls.append(('stop',))
    monkeypatch.setattr(tool,'XenovaClient',Client)
    monkeypatch.setenv('XENOVA_RESUME_BROKER_PASSWORD','test-only')
    monkeypatch.setattr('sys.argv',['send_resume','--console','--verified'])
    assert tool.main()==0
    publish=next(c for c in calls if c[0]=='publish')
    assert publish[1][2]['expected_generation']==42 and publish[2]['causation_id']=='control-id'
    assert calls[-1]==('stop',)
