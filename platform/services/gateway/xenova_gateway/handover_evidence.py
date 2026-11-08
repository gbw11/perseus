"""실제 last-A/first-B APPLIED와 원 최종 E의 bounded 판정. 연속성 권위를 만들지 않는다."""
from collections import deque


class HandoverEvidence:
    def __init__(self):
        self.pending=deque(maxlen=16);self.applied=None;self.control=None
        self.current=None;self.terminal=None

    def submission(self,row):
        if row.get('command_sequence') is not None:self.pending.append(row)
        if self.current is not None and (row.get('output_kind')!='DRIVE' or not row.get('v_mps')):
            self.current['stop_submitted']=True

    def transition(self,machine,now):
        control=(machine.generation,machine.active_vm,machine.active_app)
        previous=self.control;self.control=control
        if previous is None or previous[1]==control[1]:return
        if self.current is not None:self.finish('superseded')
        original=self.applied
        if original is not None and (original['generation'],original['source']['vm_id'],original['source']['app_id'])!=previous:
            original=None
        self.current=dict(from_vm=previous[1],to_vm=control[1],generation=control[0],started_pi_us=now,
            original=None if original is None else dict(original),first_b=None,intermediate_stop=False,
            stop_submitted=False,carry_authorized=previous[2]==control[2] and machine._handoff_grace_until*1000000>now,
            classification='pending',reason=None)

    def feedback(self,event,machine):
        if event.get('status')!='APPLIED':return
        row=next((item for item in reversed(self.pending) if item.get('command_sequence')==event.get('command_sequence')
            and item.get('pi_session')==event.get('pi_session') and item.get('device_session')==event.get('device_session')
            and item['generation']==event.get('command_generation') and item['frame_counter']==event.get('command_counter')),None)
        if row is None:return
        if row.get('output_kind')!='DRIVE' or not row.get('v_mps'):
            self.applied=None;machine.actual_output_witness=None
            if self.current is not None:self.current['intermediate_stop']=True
            return
        applied={**row,'applied_pi_lower_us':event.get('applied_pi_lower_us'),
            'applied_pi_upper_us':event.get('applied_pi_upper_us')}
        self.applied=applied;machine.actual_output_witness=applied
        current=self.current
        if current is None and self.terminal is not None and self.terminal['first_b'] is None:
            if row.get('source',{}).get('vm_id')==self.terminal['to_vm'] and row['generation']==self.terminal['generation']:
                self.terminal['first_b']=applied
        if current is None or row.get('source',{}).get('vm_id')!=current['to_vm'] or row['generation']!=current['generation']:return
        current['first_b']=applied;original=current['original']
        reason=None
        if current['intermediate_stop']:reason='intermediate_stop_applied'
        elif current['stop_submitted']:reason='stop_application_unconfirmed'
        elif not current['carry_authorized']:reason='original_carry_unconfirmed'
        elif original is None:reason='original_output_unconfirmed'
        elif (original.get('applied_pi_upper_us') is None or applied.get('applied_pi_upper_us') is None
                or applied.get('applied_pi_lower_us') is None):reason='applied_time_unconfirmed'
        elif (original.get('pi_session'),original.get('device_session'))!=(applied.get('pi_session'),applied.get('device_session')):
            reason='device_session_changed'
        elif applied['applied_pi_upper_us']>=original.get('original_expires_pi_us',0):reason='original_budget_exhausted'
        self.finish(reason)

    def finish(self,reason):
        if self.current is None:return
        self.current['classification']='continuous' if reason is None else 'stop_then_resume'
        self.current['reason']=reason;self.terminal=self.current;self.current=None

    def tick(self,now):
        if self.current is not None:
            original=self.current['original']
            if original is None or now>=original.get('original_expires_pi_us',0):self.finish('original_budget_exhausted')
