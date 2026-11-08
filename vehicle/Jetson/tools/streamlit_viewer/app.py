"""별도 PC Streamlit 관측기. 주행 조작은 기존 터미널에 남기며 제품 import/driver가 없다."""
from pathlib import Path
import time
from uuid import uuid4

import streamlit as st

from adapter import OfflineDataset
from render import map_svg,preview
from snapshot import Result,SnapshotReader,age


st.set_page_config(page_title='RC 주행 관측',page_icon='🚗',layout='wide')
st.title('RC 주행 관측')
st.caption('PC 별도 읽기 전용 · 터미널 조작 유지 · LIVE observer 미연결 · 화면 종료는 제품에 신호를 보내지 않음')
with st.sidebar:
    st.subheader('관측 입력')
    mode=st.selectbox('형식',('snapshot.json','SyntheticDatasetWriter offline'))
    folder=st.text_input('PC 관측 폴더',placeholder=r'C:\drive-observations')
    ordinal=int(st.number_input('Offline ordinal (-1 = 마지막)',min_value=-1,max_value=255,value=-1,disabled=mode=='snapshot.json'))
    show_gt=st.checkbox('snapshot의 GT 층 표시',value=False)
    st.caption('GT는 뷰어 표시용입니다. oracle.jsonl은 읽지 않으며 GT를 제품에 전달하지 않습니다.')
    st.caption('화면 갱신2Hz · 주행 계산과 독립인 진단 표시 주기')


def value(item):return 'UNKNOWN' if item is None else str(item)


@st.fragment(run_every=.5)
def observe():
    key=(mode,str(Path(folder).expanduser()) if folder else '')
    if st.session_state.get('input_key')!=key:
        old=st.session_state.get('reader')
        if old is not None:old.close()
        st.session_state.input_key=key
        st.session_state.reader=(SnapshotReader(folder) if mode=='snapshot.json' else OfflineDataset(folder)) if folder else None
        st.session_state.previews={}
    reader=st.session_state.get('reader')
    result=(reader.read() if mode=='snapshot.json' else reader.read(ordinal)) if reader is not None else Result('UNAVAILABLE',None,'관측 폴더를 지정하세요',None,None)
    body=result.snapshot.body if result.snapshot else {}
    if result.state=='INVALID':st.error('INVALID · '+str(result.reason))
    elif result.state=='STALE':st.warning('STALE · 같은 파일 재조회는 원 수신 시각과 순번을 갱신하지 않습니다')
    elif result.state=='UNAVAILABLE':st.info('UNAVAILABLE · '+str(result.reason))
    elif result.state=='HISTORICAL':st.info('HISTORICAL · 합성 offline 기록. 현재 차량·LIVE 상태가 아닙니다')
    else:st.caption('파일 형식·선택 media 해시 검사 통과. 제품 제어/READY 수락 증거와 별개입니다')
    if body.get('source',{}).get('kind')=='OFFLINE_SYNTHETIC' and result.state!='HISTORICAL':
        st.info('HISTORICAL · 합성 기록 또는 표시 자료입니다. 현재 차량의 관측값이 아닙니다')
    if 'clock_epoch' not in st.session_state:st.session_state.clock_epoch=str(uuid4())
    local=dict(domain='viewer-monotonic-us',epoch=st.session_state.clock_epoch,us=time.monotonic_ns()//1000)
    source=body.get('source',{});source_age=age(source.get('stamp'),local)
    st.caption(f"source={value(source.get('id'))} · epoch={value(source.get('epoch'))} · seq={value(source.get('sequence'))}")
    st.caption(f"뷰어 최초 수신 후={value(round(result.received_age_s,3) if result.received_age_s is not None else None)}s · "
               f"입력 age={source_age['state']} / {value(source_age['age_us'])}µs · 서로 다른 clock은 빼지 않습니다")
    left,right=st.columns(2)
    with left,st.container(border=True):
        st.subheader('전체 코스 · 지도 · 경로')
        st.caption('GT 보라 점선 / 추정 파랑 / 불확실성 주황 / 경로 민트 · 같은 frame만 겹침')
        try:
            svg=map_svg(body,show_gt=show_gt)
            if svg:st.image(svg,width='stretch')
            else:st.info('UNKNOWN · 지도/코스/경로 artifact 미연결')
        except ValueError as error:st.error(str(error))
    with right,st.container(border=True):
        st.subheader('원 RGB · YOLOP · 깊이 · grid')
        media={x.descriptor['panel']:x for x in result.snapshot.media} if result.snapshot else {}
        cached=st.session_state.previews;next_cache={}
        for tab,name in zip(st.tabs(('RGB','YOLOP','Native depth','Grid'),key='perception-panel',on_change='rerun'),('rgb','yolop','depth','grid')):
            with tab:
                item=media.get(name)
                if item is None:st.info('UNKNOWN · 원 artifact 없음');continue
                d=item.descriptor;cache_key=(name,d['sha256'])
                try:
                    png=cached.get(cache_key)
                    if png is None:png=preview(item)
                    next_cache[cache_key]=png;st.image(png,width='stretch')
                    st.caption(f"{d['encoding']} {d['width']}×{d['height']} · seq={value(d.get('source_sequence'))} · sha256={d['sha256']}")
                    original_stamp=d.get('original_stamp',d.get('stamp'))
                    if original_stamp is None:st.caption('원 시각: UNKNOWN')
                    else:st.json(original_stamp)
                    if d['encoding']=='NV12':st.caption('표시용 BT.601 limited NV12 변환/축소. 원 payload 보존, 모델 재추론 없음')
                    if d['encoding']=='RAW16_LE':st.caption('0=UNKNOWN(검정). RIGHT native grid, RGB 픽셀 정렬 없음. '+('색 범위0~3m' if d.get('depth_unit')=='mm' else '단위 UNKNOWN / RAW16 범위'))
                except Exception as error:st.error('MEDIA INVALID · '+str(error))
        st.session_state.previews=next_cache
    left,right=st.columns(2)
    with left,st.container(border=True):
        st.subheader('운영 상태 · 원 보고값')
        operation=body.get('operation') or {}
        live=result.state=='VALID' and source_age['state']=='MAPPED' and source.get('kind')=='DIAGNOSTIC'
        rows=[]
        for label,name in (('모드','mode'),('차량 상태','vehicle_phase'),('주차 ON/OFF','parking_enabled'),
                           ('A/B active','active_vm'),('standby','standby_vm'),('READY 보고','auto_ready'),('차단 사유','blocked_reason')):
            reported=operation.get(name)
            rows.append(dict(항목=label,현재='UNVERIFIED' if live and reported is not None else 'UNKNOWN',원보고=value(reported)))
        st.table(rows)
        st.caption('이 표는 Authority/장치 적용을 판정하지 않습니다. STALE·다른 clock의 옛 READY를 정상 표시하지 않습니다.')
        st.caption('수동/자동·주차·WASD 조작은 기존 터미널에서 수행합니다. 이 화면에는 주행 버튼이 없습니다.')
    with right,st.container(border=True):
        st.subheader('장치 · clock · 순번 · 처리율')
        identities=body.get('identities')
        if identities:st.json(identities)
        else:st.info('UNKNOWN · Jetson/RPi/ESP32/카메라 원 식별자 미연결')
        metrics=body.get('metrics')
        if metrics:st.json(metrics)
        else:st.caption('처리 FPS / 각 단계 지연 / 원 clock lease / 장치 연결: UNKNOWN (viewer 갱신률과 다름)')
        if source.get('stamp') is None:st.caption('원 시각: UNKNOWN')
        else:st.json(source['stamp'])
    if result.last_error:
        with st.expander('최근 원 오류 (복구 뒤에도 보존)'):
            st.json(result.last_error)
            owner=reader.reader if isinstance(reader,OfflineDataset) else reader
            if owner is not None and owner.error_raw is not None:
                st.download_button('최근 거절 원 bytes',owner.error_raw,file_name='rejected-input.bin',key='raw-rejected')
    if result.snapshot:
        with st.expander('원본·검산 정보'):
            st.code(result.snapshot.sha256,language=None)
            st.download_button('관측 snapshot 원 bytes',result.snapshot.raw,file_name='viewer-snapshot.json',key='raw-snapshot')
            if result.snapshot.raw_payload is not None:
                st.download_button('원 payload/index record bytes',result.snapshot.raw_payload,file_name='original-payload.bin',key='raw-payload')
            st.json(body)


observe()
