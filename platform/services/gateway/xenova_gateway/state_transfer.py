"""Actor-owned bounded accepted copies/application proofs; no planner or fsync."""
from collections import OrderedDict
from xenova_mqtt import handoff
from xenova_mqtt.drive_binding import app_instance
from .planning import context


class StateCopies:
    def __init__(self,machine):
        self.m=machine;self.copies=OrderedDict();self.applications={}
        self.accepted=0;self.rejected=0;self.evicted=0

    def receive(self,message):
        m=self.m;envelope=message.envelope;vm=envelope.get('vm_id');app=envelope.get('app_id');now=m.clock()
        binding=m.bindings.current(vm,now)
        if (message.retained or vm not in m.vm_ids or app not in ('autonomous','manual') or not binding or
                binding.get('app_state_models',{}).get(app)!='stateful' or
                not m.bindings.accepts(vm,envelope.get('boot_id'),envelope.get('agent_instance_id'),now,app=app)):
            self.rejected+=1;return False
        try:
            if message.type=='app.state.snapshot':
                body=handoff.snapshot(message.payload)
                if (vm!=m.active_vm or app!=m.active_app or not m.output_permitted or m.authority.protected(vm) or
                        body['source_generation']!=m.generation or body['execution_id']!=binding['execution_id'] or
                        any(body[k]!=binding[k] for k in ('intent_revision','lifecycle_generation'))):
                    raise ValueError('snapshot producer no longer has output authority')
                identity=handoff.canonical(envelope['message_id'])
                previous=[r for r in self.copies.values() if r['vm']==vm and r['app']==app and r['source']==context(binding,app)]
                if previous and handoff.counter(body['state_revision'])<=handoff.counter(previous[-1]['payload']['state_revision']):
                    raise ValueError('state revision not increasing')
                self.copies[identity]=dict(vm=vm,app=app,source=context(binding,app),payload=body)
                if len(self.copies)>8:self.copies.popitem(last=False);self.evicted+=1
            elif message.type=='app.state.applied':
                body=handoff.applied(message.payload)
                if (body['execution_id']!=binding['execution_id'] or
                        any(body[k]!=binding[k] for k in ('intent_revision','lifecycle_generation'))):
                    raise ValueError('application target changed')
                if body['phase']=='REJECTED':
                    self.applications.pop((vm,app),None);return False
                copy=self.copies.get(body['copy_message_id'])
                if body['origin']=='handoff' and (not copy or copy['app']!=app or
                        body['state_revision']!=copy['payload']['state_revision'] or
                        not handoff.compatible(copy['payload']['context'],body['context'],copy['payload']['maneuver'])):
                    raise ValueError('no compatible fully accepted source copy')
                self.applications[vm,app]=dict(target=context(binding,app),payload=body)
            else:return False
        except (ValueError,KeyError,TypeError):
            self.rejected+=1;return False
        self.accepted+=1;return True

    def evidence(self,vm,app):
        binding=self.m.bindings.current(vm,self.m.clock());proof=self.applications.get((vm,app))
        if not binding or not proof or proof['target']!=context(binding,app):return None
        value=proof['payload']
        copy=self.copies.get(value['copy_message_id'])
        if value['origin']=='handoff' and copy is None:return None
        return dict(kind='state_applied',origin=value['origin'],copy_message_id=value['copy_message_id'],
                    state_revision=value['state_revision'],context=dict(value['context']),
                    target=dict(proof['target']))

    def invalidate(self,vm,app):self.applications.pop((vm,app),None)
