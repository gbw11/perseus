import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from xenova_display.runtime_status import RuntimeStatus
from xenova_display.stream import Capture,create_stream_app
from test_outputs import config


def test_repeated_reads_share_observation_and_refresh_atomic_replacement(tmp_path,monkeypatch):
    p=tmp_path/'status';p.write_text(json.dumps({'revision':'old'}))
    now=[10.0];reads=[];original=Path.read_text
    def read(path,*a,**k):
        reads.append(path);return original(path,*a,**k)
    monkeypatch.setattr(Path,'read_text',read)
    source=RuntimeStatus(p,clock=lambda:now[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(value['revision']=='old' for value in pool.map(lambda _:source.read(),range(100)))
    assert len(reads)==1
    now[0]+=.11
    assert source.read()['revision']=='old' and len(reads)==1
    other=tmp_path/'replacement';other.write_text(json.dumps({'revision':'new'}));other.replace(p)
    now[0]+=.11
    assert source.read()['revision']=='new' and len(reads)==2
    p.unlink();now[0]+=.11
    assert source.read() is None
    p.write_text('invalid JSON');now[0]+=.11
    assert source.read() is None


def test_cache_never_renews_expired_source_or_serves_disconnected_output(tmp_path,monkeypatch):
    c=config();p=tmp_path/'status';now=[10.0]
    data={'updated_monotonic':10.0,'layout_epoch':'epoch','backend':c.backend,
          'outputs':{o.output_id:{'status':'mapped','renderer_alive':True,'connector':o.connector,
                     'rectangle':[o.x,o.y,o.width,o.height]} for o in c.outputs}}
    p.write_text(json.dumps(data));source=RuntimeStatus(p,clock=lambda:now[0])
    cap=Capture(c,c.outputs[0],p);cap.status_source=source
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    assert cap.layout() is not None
    # Same cached object and file cannot turn an expired observation into fresh.
    now[0]=16.01
    assert cap.layout() is None and source.read()['updated_monotonic']==10.0
    now[0]=16.12;data['updated_monotonic']=now[0]
    data['outputs']['monitor-1']['status']='disconnected';p.write_text(json.dumps(data))
    assert cap.layout() is None


def test_product_app_shares_one_source_between_outputs(tmp_path):
    app=create_stream_app(config(),tmp_path/'status','test-only',allow_virtual=True)
    captures=list(app.state.captures.values())
    assert captures[0].status_source is captures[1].status_source
    assert isinstance(captures[0].status_source,RuntimeStatus)
