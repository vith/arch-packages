"""Immutable controller receipts and an exact-lease pending build index."""
import base64
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import re
import subprocess
from tools import sources
from tools.recipe_gate import manifest_digest

BRANCH='controller-state'
NAMESPACES={'proposal','candidate','candidate-provenance','built','approved','acceptance','built-by-input',
            'build-request','build-result','pending','source-test','source-test-index'}
KEY=re.compile(r'^[a-zA-Z0-9_-]{1,200}$')
CONTROLS=('tools/update.py','tools/recipes.py','tools/imports.py','tools/recipe_candidates.py',
          'tools/recipe_acceptance.py','tools/recipe_state.py','tools/package_runs.py','tools/build_store.py','tools/attestations.py',
          '.github/workflows/update.yml','.github/workflows/candidate.yml',
          '.github/workflows/candidate-dispatch.yml','.github/workflows/build-package.yml',
          'tools/source_review.py','.github/workflows/verification.yml')


def digest(value):
    return hashlib.sha256(sources.canonical(value)).hexdigest()


def control_digest(root):
    return manifest_digest(root,'tools/recipe_state.py','CONTROLS')


def record_path(namespace,key):
    valid_key=isinstance(key,str) and (re.fullmatch(r'[a-z0-9][a-z0-9+_.-]{0,199}',key) if namespace=='pending' else KEY.fullmatch(key))
    if namespace not in NAMESPACES or not valid_key:
        raise ValueError('invalid controller receipt key')
    return namespace+'/'+key+'.json'


def identity_key(record):
    from tools import update as u
    values=[record['base'],record.get('recipe_base',record['base']),record['head']]
    if any(not u.SHA.fullmatch(v) for v in values):
        raise ValueError('invalid receipt identity')
    return '-'.join(values)


def decode(envelope):
    if not isinstance(envelope,dict) or set(envelope)!={'schema','digest','value'} or envelope['schema']!=1 or envelope['digest']!=digest(envelope['value']):
        raise ValueError('controller receipt digest mismatch')
    return envelope['value']


_ACTIVE_SNAPSHOT=ContextVar('controller_state_snapshot',default=None)


class StateSnapshot:
    """An immutable state tip with operation-local direct-read caches."""
    def __init__(self):
        from tools import update as u
        self._tip=u.ref_head(BRANCH)
        if self.tip is not None and (not isinstance(self.tip,str) or not u.SHA.fullmatch(self.tip)):
            raise ValueError('invalid controller state tip')
        self._values={}
        self._tree=None

    @property
    def tip(self):
        return self._tip

    def tree(self):
        from tools import update as u
        if self._tree is None:
            if not self.tip:
                self._tree=[]
            else:
                commit=u.api(u.route('/git/commits/'+self.tip))
                tree=u.api(u.route('/git/trees/'+commit['tree']['sha']+'?recursive=1'))
                if tree.get('truncated'):raise ValueError('truncated controller state')
                self._tree=tree['tree']
        return self._tree

    def load_many(self,pairs):
        from tools import update as u
        pairs=list(dict.fromkeys(pairs))
        for namespace,key in pairs:record_path(namespace,key)
        missing=[pair for pair in pairs if pair not in self._values]
        if not self.tip:
            self._values.update({pair:None for pair in missing})
        for offset in range(0,len(missing) if self.tip else 0,100):
            batch=missing[offset:offset+100]
            fields=' '.join('r'+str(i)+': object(expression: '+json.dumps(self.tip+':'+record_path(*pair))+') { __typename ... on Blob { byteSize isBinary text } }' for i,pair in enumerate(batch))
            owner,name=u.repository().split('/')
            query='query { repository(owner: '+json.dumps(owner)+', name: '+json.dumps(name)+') { '+fields+' } }'
            response=u.api('graphql','POST',{'query':query})
            if not isinstance(response,dict) or response.get('errors'):
                raise ValueError('controller state GraphQL read failed')
            rows=response.get('data',{}).get('repository')
            if not isinstance(rows,dict):raise ValueError('invalid controller state GraphQL repository')
            for i,pair in enumerate(batch):
                alias='r'+str(i)
                if alias not in rows:raise ValueError('missing controller state GraphQL object')
                row=rows[alias]
                value=None
                if row is not None:
                    if (not isinstance(row,dict) or row.get('__typename')!='Blob' or row.get('isBinary') is not False
                            or not isinstance(row.get('byteSize'),int) or not 0<=row['byteSize']<=u.MAX_TREE
                            or not isinstance(row.get('text'),str)):
                        raise ValueError('invalid controller receipt object')
                    raw=row['text'].encode()
                    if len(raw)!=row['byteSize'] or len(raw)>u.MAX_TREE:
                        raise ValueError('controller receipt exceeds bound')
                    value=decode(json.loads(raw))
                self._values[pair]=value
        return {pair:copy.deepcopy(self._values[pair]) for pair in pairs}

    def load_optional(self,namespace,key):
        return self.load_many([(namespace,key)])[(namespace,key)]

    def load(self,namespace,key):
        value=self.load_optional(namespace,key)
        if value is None:raise ValueError('missing durable '+namespace+' receipt')
        return value

    def keys(self,namespace,prefix=''):
        if prefix:record_path(namespace,prefix)
        elif namespace not in NAMESPACES:raise ValueError('invalid controller receipt namespace')
        result=[]
        for row in self.tree():
            path=row['path']
            if not path.startswith(namespace+'/'+prefix) or not path.endswith('.json'):continue
            key=path[len(namespace)+1:-5]
            record_path(namespace,key)
            if row['mode']!='100644' or row['type']!='blob':
                raise ValueError('invalid controller receipt object')
            result.append(key)
        return sorted(result)


@contextmanager
def read_snapshot():
    current=_ACTIVE_SNAPSHOT.get()
    snapshot=current if current is not None else StateSnapshot()
    token=_ACTIVE_SNAPSHOT.set(snapshot)
    try:yield snapshot
    finally:_ACTIVE_SNAPSHOT.reset(token)


def load_optional(namespace,key):
    snapshot=_ACTIVE_SNAPSHOT.get()
    return (snapshot if snapshot is not None else StateSnapshot()).load_optional(namespace,key)


def keys(namespace,prefix=''):
    snapshot=_ACTIVE_SNAPSHOT.get()
    return (snapshot if snapshot is not None else StateSnapshot()).keys(namespace,prefix)


def load(namespace,key):
    value=load_optional(namespace,key)
    if value is None:raise ValueError('missing durable '+namespace+' receipt')
    return value


def load_many(pairs):
    snapshot=_ACTIVE_SNAPSHOT.get()
    return (snapshot if snapshot is not None else StateSnapshot()).load_many(pairs)


def _write(changes,previous,message):
    from tools import update as u
    entries=[]
    for (namespace,key),value in changes.items():
        raw=sources.canonical({'schema':1,'digest':digest(value),'value':value})
        if len(raw)>u.MAX_TREE:raise ValueError('controller receipt exceeds bound')
        blob=u.api(u.route('/git/blobs'),'POST',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})['sha']
        entries.append({'path':record_path(namespace,key),'mode':'100644','type':'blob','sha':blob})
    body={'tree':entries}
    if previous:body['base_tree']=u.api(u.route('/git/commits/'+previous))['tree']['sha']
    tree=u.api(u.route('/git/trees'),'POST',body)['sha']
    commit=u.api(u.route('/git/commits'),'POST',{'tree':tree,'parents':[previous] if previous else [],'message':message})['sha']
    sources.git('fetch','--no-recurse-submodules','--no-write-fetch-head','https://github.com/'+u.repository()+'.git',commit,cwd=u.ROOT)
    try:u.push_ref(u.ROOT,commit,BRANCH,previous)
    except subprocess.CalledProcessError:
        if u.ref_head(BRANCH)==previous:raise
        return False
    return True


def save(namespace,key,value):
    if namespace in ('pending','source-test-index'):
        raise ValueError('mutable controller indexes require an exact CAS')
    record_path(namespace,key)
    for attempt in range(32):
        snapshot=StateSnapshot()
        existing=snapshot.load_optional(namespace,key)
        if existing is not None:
            if existing!=value:raise ValueError('immutable controller receipt differs')
            return digest(value)
        if _write({(namespace,key):value},snapshot.tip,'Retain '+record_path(namespace,key)):
            return digest(value)
    raise ValueError('controller receipt contention exceeds retry bound')


def _request(record):
    from tools import update as u
    if not isinstance(record,dict):raise ValueError('invalid build request')
    record_path('build-request',record.get('request_id'))
    name=record.get('pkgbase')
    if not isinstance(name,str) or not u.NAME.fullmatch(name):raise ValueError('invalid build package')
    record_path('pending',name)
    if any(not isinstance(record.get(key),str) or not u.SHA.fullmatch(record[key]) for key in ('base','head')):
        raise ValueError('invalid build request identity')
    if record.get('repository')!=u.repository():raise ValueError('build request repository mismatch')
    number=record.get('pr_number')
    if isinstance(number,bool) or not str(number).isdecimal() or int(number)<1:
        raise ValueError('invalid build request PR')
    for key in ('recipe_base','recipe_tree'):
        if key in record and (not isinstance(record[key],str) or not u.SHA.fullmatch(record[key])):
            raise ValueError('invalid build recipe identity')
    packages=record.get('packages')
    if not isinstance(packages,list) or len(packages)!=1 or not isinstance(packages[0],dict) or packages[0].get('pkgbase')!=name:
        raise ValueError('build request must contain its exact package')
    package=packages[0]
    if package.get('recipe_commit')!=record['head']:raise ValueError('build recipe head mismatch')
    lock=package.get('lock')
    sources_=lock.get('sources') if isinstance(lock,dict) else None
    if not isinstance(sources_,list) or any(not isinstance(source,dict) for source in sources_):
        raise ValueError('invalid build source targets')
    fields=('id','kind','source','url','ref','commit','tag_object','peeled_commit','release_id','asset_id','checksums','git_context','version_method')
    targets=[{key:source[key] for key in fields if key in source} for source in sources_]
    ids=[source.get('id') for source in targets]
    if any(not isinstance(value,str) or not value for value in ids) or len(set(ids))!=len(ids):
        raise ValueError('invalid duplicate build source targets')
    return name,targets


def build_request(request_id,snapshot=None):
    return (snapshot if snapshot is not None else (_ACTIVE_SNAPSHOT.get() or StateSnapshot())).load('build-request',request_id)


def pending_build(pkgbase,snapshot=None):
    return (snapshot if snapshot is not None else (_ACTIVE_SNAPSHOT.get() or StateSnapshot())).load_optional('pending',pkgbase)


def reserve_build(record,retry=False):
    name,targets=_request(record)
    request_id=record['request_id']
    observed=False
    expected=None
    for attempt in range(32):
        snapshot=StateSnapshot()
        values=snapshot.load_many([('pending',name),('build-request',request_id)])
        current=values[('pending',name)]
        if current is not None:
            _active(snapshot,name,current.get('request_id') if isinstance(current,dict) else None)
        if not observed:
            expected=record.get('expected_pending',copy.deepcopy(current))
            observed=True
        stored=values[('build-request',request_id)]
        if stored is not None and stored!=record:raise ValueError('immutable build request differs')
        if current is not None:
            same=current['head']==record['head'] and current['targets']==targets
            if (current['state'] in ('queued','running') or same and (current['state']=='success' or not retry)):
                return current,False
            if current['request_id']==request_id:
                raise ValueError('retry requires a new build request identity')
        if expected!=current and expected!=(current or {}).get('request_id'):
            return current,False
        pending={'schema':1,'pkgbase':name,'request_id':request_id,'head':record['head'],
                 'targets':targets,'state':'queued','worker':None,'result':None}
        if 'source_epoch' in record:pending['source_epoch']=record['source_epoch']
        changes={('pending',name):pending}
        if stored is None:changes[('build-request',request_id)]=record
        if _write(changes,snapshot.tip,'Reserve build '+request_id):return pending,True
    raise ValueError('controller receipt contention exceeds retry bound')


def _producer(producer,record):
    from tools import update as u
    if not isinstance(producer,dict) or producer.get('legacy'):
        raise ValueError('invalid build producer')
    for key in ('run_id','run_attempt'):
        value=producer.get(key)
        if isinstance(value,bool) or not isinstance(value,(str,int)) or len(str(value))>20 or not str(value).isdecimal() or int(value)<1:
            raise ValueError('invalid build producer identity')
    if (producer.get('path')!='.github/workflows/build-package.yml'
            or producer.get('pkgbase',record['pkgbase'])!=record['pkgbase']):
        raise ValueError('build producer does not match request')
    if not isinstance(producer.get('head_sha'),str) or not u.SHA.fullmatch(producer['head_sha']):
        raise ValueError('invalid producer head')
    return copy.deepcopy(producer)


def _active(snapshot,pkgbase,request_id):
    record=snapshot.load('build-request',request_id)
    _request(record)
    pending=snapshot.load('pending',pkgbase)
    if not isinstance(pending,dict) or record['pkgbase']!=pkgbase or pending.get('request_id')!=request_id:
        raise ValueError('stale build request')
    _,targets=_request(record)
    if (pending.get('schema')!=1 or pending.get('pkgbase')!=pkgbase
            or pending.get('head')!=record['head'] or pending.get('targets')!=targets
            or pending.get('state') not in ('queued','running','success','failure')
            or 'worker' not in pending or 'result' not in pending):
        raise ValueError('invalid pending build identity')
    return record,pending


def register_build(pkgbase,request_id,producer):
    for attempt in range(32):
        snapshot=StateSnapshot()
        record,pending=_active(snapshot,pkgbase,request_id)
        worker=_producer(producer,record)
        if pending['worker'] is not None:
            if pending['worker']!=worker:raise ValueError('build request already has a different worker')
            return pending
        if pending['state']!='queued':raise ValueError('build request is not queued')
        pending={**pending,'state':'running','worker':worker}
        if _write({('pending',pkgbase):pending},snapshot.tip,'Register build '+request_id):return pending
    raise ValueError('controller receipt contention exceeds retry bound')


def put_build_result(request_id,descriptor):
    snapshot=StateSnapshot()
    record=snapshot.load('build-request',request_id)
    _request(record)
    if (not isinstance(descriptor,dict) or descriptor.get('request_id')!=request_id
            or descriptor.get('pkgbase')!=record['pkgbase'] or descriptor.get('record')!=record):
        raise ValueError('build result request identity mismatch')
    worker=_producer(descriptor.get('producer'),record)
    def validate(view):
        _,pending=_active(view,record['pkgbase'],request_id)
        if pending['state'] not in ('running','success') or pending['worker']!=worker:
            raise ValueError('stale build result producer')
    for attempt in range(32):
        snapshot=StateSnapshot()
        validate(snapshot)
        existing=snapshot.load_optional('build-result',request_id)
        if existing is not None:
            if existing!=descriptor:raise ValueError('immutable build result differs')
            return digest(descriptor)
        if snapshot.load('pending',record['pkgbase'])['state']!='running':
            raise ValueError('completed build result is missing')
        if _write({('build-result',request_id):descriptor},snapshot.tip,'Retain build result '+request_id):
            return digest(descriptor)
    raise ValueError('controller receipt contention exceeds retry bound')


def finish_build(pkgbase,request_id,outcome,result=None):
    if outcome not in ('success','failure'):raise ValueError('invalid whole build outcome')
    for attempt in range(32):
        snapshot=StateSnapshot()
        record,pending=_active(snapshot,pkgbase,request_id)
        if pending['state'] in ('success','failure'):
            if pending['state']!=outcome:raise ValueError('completed build outcome differs')
            if result is not None and outcome=='success' and result!=snapshot.load('build-result',request_id):
                raise ValueError('completed build result differs')
            return pending
        if pending['worker'] is None:raise ValueError('build worker has not registered')
        if outcome=='success':
            descriptor=snapshot.load('build-result',request_id)
            if (descriptor.get('request_id')!=request_id or descriptor.get('record')!=record
                    or descriptor.get('pkgbase')!=pkgbase or descriptor.get('producer')!=pending['worker']
                    or result is not None and result!=descriptor):
                raise ValueError('build result producer mismatch')
        elif result is not None:raise ValueError('failed worker cannot retain a successful result')
        pending={**pending,'state':outcome,'result':request_id if outcome=='success' else None}
        if _write({('pending',pkgbase):pending},snapshot.tip,'Finish build '+request_id):return pending
    raise ValueError('controller receipt contention exceeds retry bound')


def abandon_build(pkgbase,request_id,*,retry=False):
    """End an unregistered request after supersession or explicit manager retry."""
    from tools import update as u
    expected=None
    for attempt in range(32):
        snapshot=StateSnapshot()
        record,pending=_active(snapshot,pkgbase,request_id)
        if pending['state']=='failure' and pending['worker'] is None:
            return pending
        if pending['state']!='queued' or pending['worker'] is not None:
            raise ValueError('registered build cannot be abandoned')
        if expected is None:expected=copy.deepcopy(pending)
        elif pending!=expected:raise ValueError('pending build changed during abandonment')
        if not retry:
            pr=u.api(u.route('/pulls/'+str(record['pr_number'])))
            if (not isinstance(pr,dict) or pr.get('number')!=int(record['pr_number'])
                    or pr.get('state') not in ('open','closed')
                    or pr.get('base',{}).get('ref')!='pkg/'+pkgbase
                    or pr.get('base',{}).get('sha')!=record.get('recipe_base')
                    or any(pr.get(side,{}).get('repo',{}).get('full_name')!=record['repository']
                           for side in ('base','head'))
                    or not isinstance(pr.get('head',{}).get('sha'),str)
                    or not u.SHA.fullmatch(pr['head']['sha'])
                    or record.get('head_branch') is not None and pr['head'].get('ref')!=record['head_branch']):
                raise ValueError('abandoned build PR identity mismatch')
            if pr['state']!='closed' and pr['head']['sha']==record['head']:
                raise ValueError('unchanged queued build requires explicit retry')
        failed={**pending,'state':'failure','result':None}
        if _write({('pending',pkgbase):failed},snapshot.tip,'Abandon build '+request_id):
            return failed
    raise ValueError('controller receipt contention exceeds retry bound')


def _source_test_identity(evidence):
    from tools import update as u
    if (not isinstance(evidence,dict) or evidence.get('schema')!=1
            or evidence.get('repository')!=u.repository()):
        raise ValueError('invalid source test evidence')
    for field in ('base','head','tree'):
        if not isinstance(evidence.get(field),str) or not u.SHA.fullmatch(evidence[field]):
            raise ValueError('invalid source test identity')
    producer=evidence.get('producer')
    if not isinstance(producer,dict):raise ValueError('invalid source test producer')
    numbers=[]
    for field in ('run_id','run_attempt'):
        value=producer.get(field)
        if (isinstance(value,bool) or not isinstance(value,(str,int))
                or len(str(value))>20 or not str(value).isdecimal() or int(value)<1):
            raise ValueError('invalid source test issuer')
        numbers.append(int(value))
    if (not isinstance(producer.get('head_sha'),str) or not u.SHA.fullmatch(producer['head_sha'])
            or producer['head_sha']!=evidence['base']
            or producer.get('path') not in ('.github/workflows/candidate.yml',
                                           '.github/workflows/verification.yml',
                                           '.github/workflows/publish.yml')):
        raise ValueError('invalid source test producer identity')
    key=evidence['head']+'-'+str(numbers[0])+'-'+str(numbers[1])
    record_path('source-test',key)
    return key,tuple(numbers)


def _source_test_pointer(head,pointer):
    from tools import update as u
    if not isinstance(head,str) or not u.SHA.fullmatch(head):
        raise ValueError('invalid source test head')
    record_path('source-test-index',head)
    if (not isinstance(pointer,dict) or set(pointer)!={'schema','head','key','run_id','run_attempt'}
            or pointer['schema']!=1 or pointer['head']!=head):
        raise ValueError('invalid source test index')
    numbers=[]
    for field in ('run_id','run_attempt'):
        value=pointer[field]
        if (not isinstance(value,str) or len(value)>20 or not value.isdecimal()
                or int(value)<1 or str(int(value))!=value):
            raise ValueError('invalid source test index issuer')
        numbers.append(int(value))
    if pointer['key']!=head+'-'+pointer['run_id']+'-'+pointer['run_attempt']:
        raise ValueError('source test index identity mismatch')
    record_path('source-test',pointer['key'])
    return tuple(numbers)


def save_source_test(evidence):
    """Store one immutable issuer receipt and advance its exact-head index."""
    key,numbers=_source_test_identity(evidence)
    head=evidence['head']
    pointer={'schema':1,'head':head,'key':key,
             'run_id':str(numbers[0]),'run_attempt':str(numbers[1])}
    for attempt in range(32):
        snapshot=StateSnapshot()
        values=snapshot.load_many([('source-test',key),('source-test-index',head)])
        existing=values[('source-test',key)]
        current=values[('source-test-index',head)]
        if existing is not None and existing!=evidence:
            raise ValueError('immutable source test evidence differs')
        current_numbers=_source_test_pointer(head,current) if current is not None else None
        changes={}
        if existing is None:changes[('source-test',key)]=evidence
        if current_numbers is None or numbers>current_numbers:
            changes[('source-test-index',head)]=pointer
        elif numbers==current_numbers and current!=pointer:
            raise ValueError('source test issuer index differs')
        if not changes:return key
        if _write(changes,snapshot.tip,'Retain source test '+key):return key
    raise ValueError('controller receipt contention exceeds retry bound')


def source_test_evidence(head,snapshot=None):
    from tools import update as u
    if not isinstance(head,str) or not u.SHA.fullmatch(head):
        raise ValueError('invalid source test head')
    snapshot=snapshot if snapshot is not None else (_ACTIVE_SNAPSHOT.get() or StateSnapshot())
    pointer=snapshot.load_optional('source-test-index',head)
    if pointer is None:return None
    numbers=_source_test_pointer(head,pointer)
    evidence=snapshot.load('source-test',pointer['key'])
    key,evidence_numbers=_source_test_identity(evidence)
    if evidence['head']!=head or key!=pointer['key'] or evidence_numbers!=numbers:
        raise ValueError('source test indexed evidence identity mismatch')
    return evidence


def is_attested(namespace,key,value,head):
    from tools import update as u
    record_path(namespace,key)
    if not u.SHA.fullmatch(head):raise ValueError('invalid attestation head')
    context='arch-receipt/'+namespace+'/'+key
    expected=digest(value)
    page=1
    while True:
        rows=u.api(u.route('/commits/'+head+'/statuses?per_page=100&page='+str(page)))
        if not isinstance(rows,list) or len(rows)>100:
            raise ValueError('invalid receipt attestation status page')
        if any(row.get('context')==context and row.get('state')=='success'
               and row.get('description')==expected
               and row.get('creator',{}).get('login')=='github-actions[bot]' for row in rows):
            return True
        if len(rows)<100:return False
        page+=1


def attest(namespace,key,value,head):
    from tools import update as u
    if is_attested(namespace,key,value,head):return
    context='arch-receipt/'+namespace+'/'+key
    posted=u.api(u.route('/statuses/'+head),'POST',{'state':'success',
          'context':context,'description':digest(value)})
    if (posted.get('creator',{}).get('login')!='github-actions[bot]'
            or posted.get('context')!=context or posted.get('state')!='success'
            or posted.get('description')!=digest(value)):
        raise ValueError('receipt attestation requires trusted Actions bot execution')


def require_attestation(namespace,key,value,head):
    if not is_attested(namespace,key,value,head):
        raise ValueError('missing exact trusted receipt attestation')


def protected_branch(name):
    from tools import update as u
    if not u.NAME.fullmatch(name):raise ValueError('invalid package branch')
    branch=u.api(u.route('/branches/pkg%2F'+name))
    if branch.get('protected') is not True:
        raise ValueError('recipe branch protection is not deployed')
    return branch


def recipe_identity(number,base=None,head=None,merged=False):
    from tools import update as u
    if not str(number).isdecimal() or int(number)<1:raise ValueError('invalid PR number')
    pr=u.api(u.route('/pulls/'+str(number)))
    name=pr['base']['ref'].removeprefix('pkg/')
    branch=pr['head']['ref']
    valid_state=(pr['state']=='closed' and pr.get('merged') and pr.get('merged_at')) if merged else pr['state']=='open'
    if not valid_state or pr['base']['ref']!='pkg/'+name or not u.NAME.fullmatch(name) or not isinstance(branch,str) or not branch or any(pr[side]['repo']['full_name']!=u.repository() for side in ('base','head')):
        raise ValueError('PR is not an open native recipe candidate')
    protected_branch(name)
    control=u.main_sha();checkout=u.trusted_checkout_sha()
    if control!=checkout or base and base!=control or head and head!=pr['head']['sha'] or not merged and u.ref_head('pkg/'+name)!=pr['base']['sha']:
        raise ValueError('candidate control/base/head changed; redispatch required')
    if any(not u.SHA.fullmatch(v) for v in (control,pr['base']['sha'],pr['head']['sha'])):
        raise ValueError('invalid recipe identities')
    return pr,control,pr['head']['sha']


def assert_recipe_identity(record,merged=False,control=None):
    from tools import update as u
    merged=merged or bool(record.get('accepted'))
    if not merged:
        pr,_,_=recipe_identity(record['pr_number'],head=record['head'])
    else:
        pr=u.api(u.route('/pulls/'+str(record['pr_number'])))
        protected_branch(record['pkgbase'])
        expected_control=control or u.main_sha()
        if pr['state']!='closed' or not pr.get('merged') or not pr.get('merged_at') or pr['head']['sha']!=record['head'] or u.main_sha()!=expected_control or u.trusted_checkout_sha()!=expected_control:
            raise ValueError('accepted recipe identity changed')
        if record.get('accepted') and pr.get('merge_commit_sha')!=record['accepted']:
            raise ValueError('accepted recipe merge changed')
    expected_branch=record['head_branch']
    if pr['base']['ref']!='pkg/'+record['pkgbase'] or pr['base']['sha']!=record['recipe_base'] or pr['head']['ref']!=expected_branch or any(pr[side]['repo']['full_name']!=u.repository() for side in ('base','head')):
        raise ValueError('recipe candidate base/head changed')
    tree=u.api(u.route('/git/commits/'+record['head']))['tree']['sha']
    if tree!=record['recipe_tree']:raise ValueError('recipe tree identity mismatch')
    return pr
