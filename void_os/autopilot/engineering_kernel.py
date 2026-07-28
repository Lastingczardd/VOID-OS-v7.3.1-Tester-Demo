from __future__ import annotations
import json, time
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class Opportunity:
    key: str; task: str; evidence: str; value: int; urgency: int; confidence: int; risk: int
    @property
    def score(self): return self.value + self.urgency + self.confidence - self.risk

class AutonomousEngineeringKernel:
    def __init__(self, cfg, router, agents, state_root: Path):
        self.cfg, self.router, self.agents = cfg, router, agents
        self.state_root = Path(state_root); self.state_root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_root/'evidence_ledger.jsonl'
        self.opportunity_path = self.state_root/'opportunities.json'
        self.experiment_path = self.state_root/'experiments.jsonl'

    def prepare_cycle(self, objective, context, recent_tasks, cycle):
        ops = self.discover_opportunities(context); history = self._load(self.opportunity_path,{})
        recent=' '.join(recent_tasks).lower()
        for op in ops:
            h=history.get(op.key,{}); penalty=min(35,int(h.get('attempts',0))*6)
            if op.task.lower() in recent: penalty += 30
            h['computed_score']=op.score-penalty; history[op.key]=h
        selected=max(ops,key=lambda o:history[o.key]['computed_score'])
        h=history[selected.key]; h.update(task=selected.task,evidence=selected.evidence,attempts=int(h.get('attempts',0))+1,last_cycle=cycle,last_selected=int(time.time()))
        self._write(self.opportunity_path,history)
        candidates=self._generate_candidates(selected,objective,context)
        winner=self._select_candidate(candidates); team=self._select_team(selected,winner)
        packet={'cycle':cycle,'opportunity':asdict(selected),'task':selected.task,'team':team,'candidates':candidates,'winner':winner,'created_at':int(time.time())}
        self._append(self.experiment_path,packet); self._ledger('cycle_prepared',packet); return packet

    def discover_opportunities(self, context):
        t=(context or '').lower(); errors=sum(t.count(x) for x in ('traceback','error:','exception','failed','winerror')); todos=t.count('todo')+t.count('fixme'); tests=t.count('test')
        out=[]; add=lambda *a: out.append(Opportunity(*a))
        if errors: add('verified_failure','Find the most repeated verified failure, implement the smallest reversible fix, and add a regression check.',f'{errors} failure markers observed',95,95,90,20)
        if todos: add('unfinished_work','Resolve the highest-impact TODO or FIXME supported by project evidence and verify adjacent behavior.',f'{todos} unfinished-work markers observed',78,70,75,18)
        if tests<5: add('critical_tests','Add the highest-value missing automated test for START, STOP, rollback, path safety, recovery, or upgrade validation.','Critical behavior has little visible automated coverage',88,72,86,12)
        add('reasoning_engine','Improve one measurable part of opportunity discovery, candidate comparison, evidence checking, or result verification.','Continuous reasoning-quality objective',82,55,70,20)
        add('creative_experiment','Identify one verified bottleneck, create three materially different solution approaches, and implement the safest high-value winner.','Continuous creative problem-solving objective',79,48,68,22)
        add('reliability','Inspect the local architecture for one reliability bottleneck and produce a bounded improvement with a clear pass/fail check.','Continuous reliability objective',80,52,72,18)
        add('operator_simplicity','Remove one unnecessary operator decision while preserving START/STOP-only operation.','Human planning must remain unnecessary',76,46,74,16)
        return out

    def outcome(self, packet, success, output, summary, implementation=None):
        event={'cycle':packet.get('cycle'),'task':packet.get('task'),'winner':packet.get('winner'),'success':bool(success),'output':output,'summary':(summary or '')[:1800],'implementation':implementation or {},'recorded_at':int(time.time())}
        self._ledger('cycle_outcome',event)
        history=self._load(self.opportunity_path,{}); key=packet.get('opportunity',{}).get('key')
        if key in history:
            history[key]['successes']=int(history[key].get('successes',0))+(1 if success else 0); history[key]['failures']=int(history[key].get('failures',0))+(0 if success else 1); history[key]['last_output']=output; self._write(self.opportunity_path,history)

    def _generate_candidates(self, op, objective, context):
        prompt=f'''Design exactly three materially different engineering approaches. Return strict JSON with key candidates. Each candidate needs name, approach, expected_benefit, simplicity, reversibility, evidence_fit, risk, verification.\nOBJECTIVE: {objective}\nTASK: {op.task}\nEVIDENCE: {op.evidence}\nCONTEXT:\n{context[:12000]}'''
        try:
            raw=self.router.generate(prompt,'reasoning',max_tokens=1500); parsed=self._extract_json(raw); vals=parsed.get('candidates',[])
            clean=[self._normalize(v,i) for i,v in enumerate(vals[:3])]
            if len(clean)==3: return clean
        except Exception: pass
        return [
            {'name':'Minimal Repair','approach':'Make the smallest targeted change supported by evidence.','expected_benefit':72,'simplicity':92,'reversibility':94,'evidence_fit':86,'risk':12,'verification':'Compile and run the nearest regression check.'},
            {'name':'Structural Improvement','approach':'Refactor the narrow subsystem while preserving interfaces.','expected_benefit':84,'simplicity':62,'reversibility':76,'evidence_fit':78,'risk':28,'verification':'Run existing tests plus a focused behavior comparison.'},
            {'name':'Experimental Alternative','approach':'Prototype a distinct implementation in isolation and keep it only if measured results improve.','expected_benefit':88,'simplicity':48,'reversibility':88,'evidence_fit':68,'risk':35,'verification':'Benchmark against current behavior and discard on regression.'}]

    def _select_candidate(self,candidates):
        score=lambda c:c['expected_benefit']*.30+c['simplicity']*.18+c['reversibility']*.22+c['evidence_fit']*.30-c['risk']*.35
        w=dict(max(candidates,key=score)); w['selection_score']=round(score(w),2); w['selection_reason']='Highest weighted evidence fit, benefit, simplicity, and reversibility after risk penalty.'; return w

    def _select_team(self,op,winner):
        avail=set(self.agents.agents); text=(op.task+' '+winner.get('approach','')).lower(); desired=['Architect','Researcher','Coder','Test Engineer','Critic','Security Sentinel']
        if 'test' in text: desired=['Test Engineer','Debugger','Coder','Critic','Security Sentinel']
        elif any(x in text for x in ('failure','repair','error')): desired=['Debugger','Architect','Coder','Test Engineer','Critic']
        elif any(x in text for x in ('creative','three','alternative')): desired=['Architect','Creator','Product Builder','Critic','Test Engineer']
        selected=[n for n in desired if n in avail]+[n for n in sorted(avail) if n not in desired]
        return selected[:6]

    @staticmethod
    def experiment_brief(packet):
        lines=['EXPERIMENT DESIGN:']+[f"{i}. {c.get('name')}: {c.get('approach')} | verification: {c.get('verification')}" for i,c in enumerate(packet.get('candidates',[]),1)]
        w=packet.get('winner',{}); lines += [f"SELECTED APPROACH: {w.get('name')} — {w.get('approach')}",f"WHY: {w.get('selection_reason')}"]
        return '\n'.join(lines)

    def _ledger(self,event,payload): self._append(self.ledger_path,{'event':event,'time':int(time.time()),'payload':payload})
    @staticmethod
    def _append(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
    @staticmethod
    def _load(path,default):
        try:return json.loads(path.read_text(encoding='utf-8'))
        except Exception:return default
    @staticmethod
    def _write(path,value):
        tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(path)
    @staticmethod
    def _extract_json(text):
        a=text.find('{'); b=text.rfind('}'); return json.loads(text[a:b+1]) if a>=0 and b>a else {}
    @staticmethod
    def _normalize(v,i):
        v=v if isinstance(v,dict) else {}
        def n(k,d):
            try:return max(0,min(100,int(v.get(k,d))))
            except:return d
        return {'name':str(v.get('name') or f'Candidate {i+1}')[:100],'approach':str(v.get('approach') or 'Bounded improvement.')[:1200],'expected_benefit':n('expected_benefit',60),'simplicity':n('simplicity',60),'reversibility':n('reversibility',70),'evidence_fit':n('evidence_fit',60),'risk':n('risk',30),'verification':str(v.get('verification') or 'Compile and run focused tests.')[:600]}
