"""Uncredentialed discovery and immutable native source materialization."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

MAXIMUM = 805306368
FIELDS = {'id','kind','source','url','ref','commit','tag_object','peeled_commit','release_id','asset_id','checksums','bundle'}
HEX = re.compile(r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)+'\n').encode()


def public_url(url):
    from urllib.parse import urlsplit
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('source URL must be public HTTPS without credentials or fragment')
    if p.hostname in {'git.n3t.work','ci.n3t.work','arch.n3t.work','localhost'}:
        raise ValueError('unsupported runtime origin')
    return url


def fetch(url, destination=None):
    public_url(url)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'arch-packages-source'}), timeout=120) as response:
        public_url(response.url)
        data=response.read(MAXIMUM+1)
    if len(data)>MAXIMUM:
        raise ValueError('source exceeds bounded download')
    if destination is not None:
        Path(destination).write_bytes(data)
    return data


def git(*args, cwd=None, check=True):
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='https:file')
    safe=['-c','safe.directory='+str(Path(cwd).resolve())] if cwd is not None else []
    result=subprocess.run(['git',*safe,'-c','core.hooksPath=/dev/null','-c','credential.helper=','-c','protocol.file.allow=always',*map(str,args)],cwd=cwd,env=env,check=check,capture_output=True,text=True)
    return result.stdout.strip() if check else result


def refs(repository):
    rows=[]
    for line in git('for-each-ref','--format=%(refname) %(objectname) %(*objectname)',cwd=repository).splitlines():
        fields=line.split()
        rows.append({'name':fields[0],'object':fields[1],'peeled':fields[2] if len(fields)>2 else None})
    return sorted(rows,key=lambda r:r['name'])


def clone(url, directory):
    public_url(url)
    if Path(directory).exists():
        raise ValueError('source clone destination exists')
    advertised=git('ls-remote','--heads','--tags',url).splitlines()
    lengths={len(row.split()[0]) for row in advertised}
    if lengths not in ({40},{64}):
        raise ValueError('invalid or empty remote object format')
    git('init','--bare','--object-format='+('sha256' if lengths=={64} else 'sha1'),directory)
    git('fetch','--no-recurse-submodules','--',url,'+refs/heads/*:refs/heads/*','+refs/tags/*:refs/tags/*',cwd=directory)
    return Path(directory)


def _work(watcher):
    path=Path(watcher['work_dir'])
    path.mkdir(parents=True,exist_ok=True)
    return path


def discover_aur(watcher, accepted):
    package=watcher['package']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*',package):
        raise ValueError('invalid AUR package')
    repository=_work(watcher)/'aur.git'
    if repository.exists():
        raise ValueError('AUR checkout destination exists')
    git('init','--bare',repository)
    ref=watcher.get('ref','refs/heads/master')
    if not ref.startswith('refs/heads/'):
        raise ValueError('AUR watcher must enroll a branch')
    git('fetch','--no-recurse-submodules','--',watcher.get('url','https://aur.archlinux.org/'+package+'.git'),'+'+ref+':'+ref,cwd=repository)
    commit=git('rev-parse',ref+'^{commit}',cwd=repository)
    old=accepted.get('commit')
    if old:
        git('cat-file','-e',old+'^{commit}',cwd=repository)
    if old==commit:
        return None
    files=aur_files(repository,commit)
    previous_files=aur_files(repository,old) if old else []
    difference=git('diff','--no-ext-diff','--no-textconv',old,commit,cwd=repository) if old else 'Initial AUR import'
    if len(difference.encode())>1048576:
        raise ValueError('AUR diff exceeds bound')
    return {'kind':'aur','commit':commit,'previous':old,'previous_files':previous_files,'files':files,'diff':difference,'fast_forward':not old or git('merge-base','--is-ancestor',old,commit,cwd=repository,check=False).returncode==0}


def aur_files(repository,commit):
    files=[]
    for row in git('ls-tree','-r','-z',commit,cwd=repository).split('\0'):
        if not row:
            continue
        descriptor,path=row.split('\t',1)
        mode,kind,obj=descriptor.split()
        if kind!='blob' or mode not in {'100644','100755','120000'}:
            raise ValueError('AUR tree contains unsupported file type')
        if path.startswith('/') or '..' in Path(path).parts:
            raise ValueError('unsafe AUR tree path')
        data=subprocess.run(['git','--git-dir',str(repository),'cat-file','blob',obj],check=True,capture_output=True).stdout
        if len(data)>1048576:
            raise ValueError('AUR recipe file too large')
        files.append({'path':path,'mode':mode,'content':data.decode('utf-8')})
    return files


def discover_release_tag(watcher, accepted):
    repository=watcher['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        raise ValueError('invalid release repository')
    pattern=re.compile(watcher.get('tag_pattern',r'^v(\d+)\.(\d+)\.(\d+)$'))
    if accepted.get('tag'):
        previous=git('ls-remote','--tags','https://github.com/'+repository+'.git','refs/tags/'+accepted['tag']).splitlines()
        if len(previous)!=1 or accepted.get('tag_object') and previous[0].split()[0]!=accepted['tag_object']:
            raise ValueError('accepted tag retargeted or missing')
    if watcher.get('kind')=='release':
        releases=json.loads(fetch('https://api.github.com/repos/'+repository+'/releases?per_page=100'))
        choices=[r for r in releases if not r['draft'] and not r['prerelease'] and pattern.fullmatch(r['tag_name'])]
        tag_key='tag_name'
    else:
        rows=git('ls-remote','--tags','--refs','https://github.com/'+repository+'.git').splitlines()
        choices=[{'tag_name':r.split()[1].removeprefix('refs/tags/'),'object':r.split()[0]} for r in rows if pattern.fullmatch(r.split()[1].removeprefix('refs/tags/'))]
        tag_key='tag_name'
    if not choices:
        raise ValueError('no authentic stable release/tag found')
    def order(row):
        groups=pattern.fullmatch(row[tag_key]).groups()
        components = groups[0].split('.') if len(groups)==1 else groups
        if not components or any(not g.isdecimal() for g in components):
            raise ValueError('tag grammar must provide numeric version groups')
        return tuple(map(int,components))
    selected=max(choices,key=order)
    if selected['tag_name']==accepted.get('tag'):
        if selected.get('id')!=accepted.get('release_id') and watcher.get('kind')=='release':
            raise ValueError('release identity changed')
        if watcher.get('kind')=='release':
            matches=[a for a in selected['assets'] if a['name']==watcher['asset']]
            if len(matches)!=1 or accepted.get('asset_id') and matches[0]['id']!=accepted['asset_id']:
                raise ValueError('accepted release asset replaced or missing')
        # Verify tag object even when version did not advance.
        rows=git('ls-remote','--tags','https://github.com/'+repository+'.git','refs/tags/'+selected['tag_name']).splitlines()
        if len(rows)!=1 or accepted.get('tag_object') and rows[0].split()[0]!=accepted['tag_object']:
            raise ValueError('accepted tag retargeted or missing')
        return None
    return {'kind':watcher['kind'],'tag':selected['tag_name'],'version':'.'.join(map(str,order(selected))),'release_id':selected.get('id'),'assets':selected.get('assets',[]),'previous':accepted.get('tag')}


def discover_git(watcher, accepted):
    repository=clone(watcher['url'],_work(watcher)/'watched.git')
    ref=watcher['ref']
    if not ref.startswith('refs/heads/'):
        raise ValueError('watched ref must be full branch ref')
    commit=git('rev-parse','--verify',ref+'^{commit}',cwd=repository)
    context=refs(repository)
    digest=hashlib.sha256(canonical(context)).hexdigest()
    old=accepted.get('commit')
    if old:
        git('cat-file','-e',old+'^{commit}',cwd=repository)
    previous_refs=accepted.get('bundle',{}).get('refs',[]) if accepted.get('bundle') else []
    current={r['name']:r for r in context}
    authentic=all(r['name'] in current and current[r['name']]==r for r in previous_refs if r['name'].startswith('refs/tags/'))
    if not authentic:
        raise ValueError('accepted authentic tag changed or disappeared')
    if old==commit and accepted.get('bundle',{}).get('refs_digest')==digest:
        return None
    return {'kind':'git','commit':commit,'ref':ref,'refs_digest':digest,'authentic':authentic,'fast_forward':not old or git('merge-base','--is-ancestor',old,commit,cwd=repository,check=False).returncode==0}


def freeze_source(spec):
    record={key:spec.get(key) for key in FIELDS}
    record['checksums']=dict(spec.get('checksums',{}))
    if record['kind']=='local':
        return record
    public_url(record['url'])
    if record['kind']!='git':
        data=fetch(record['url'])
        for algorithm,expected in record['checksums'].items():
            if expected=='SKIP':
                continue
            if hashlib.new(algorithm,data).hexdigest()!=expected:
                raise ValueError('source checksum mismatch')
        return record
    directory=Path(spec['work_dir']); directory.mkdir(parents=True,exist_ok=True)
    repository=clone(record['url'],directory/'freeze.git')
    target=git('rev-parse','--verify',record['ref']+'^{commit}',cwd=repository)
    if record['commit'] and target!=record['commit']:
        raise ValueError('moving source changed during freeze')
    record['commit']=target
    if record['ref'].startswith('refs/tags/'):
        record['tag_object']=git('rev-parse',record['ref'],cwd=repository)
        record['peeled_commit']=target
        if spec.get('tag_object') and spec['tag_object']!=record['tag_object']:
            raise ValueError('tag changed during freeze')
    context=refs(repository)
    file=directory/'source.bundle'
    git('bundle','create',file,'--all',cwd=repository)
    digest=hashlib.sha256(file.read_bytes()).hexdigest()
    record['bundle']={'url':spec.get('bundle_url'),'sha256':digest,'object_format':git('rev-parse','--show-object-format',cwd=repository),'refs':context,'refs_digest':hashlib.sha256(canonical(context)).hexdigest()}
    return record


def validate_lock(lock, allow_unpublished=False):
    if set(lock)!= {'schema','version','sources'} or lock['schema']!=1 or not isinstance(lock['version'],str) or not lock['version']:
        raise ValueError('invalid source lock')
    ids=set()
    for source in lock['sources']:
        if set(source)!=FIELDS or source['id'] in ids or source['kind'] not in {'local','archive','release','git'}:
            raise ValueError('invalid/duplicate source record')
        ids.add(source['id'])
        if not isinstance(source['checksums'],dict) or not isinstance(source['source'],str):
            raise ValueError('invalid native source/checksums')
        if source['kind']!='local':
            public_url(source['url'])
        if source['kind']=='git':
            bundle=source['bundle']
            if not isinstance(bundle,dict) or set(bundle)!={'url','sha256','object_format','refs','refs_digest'}:
                raise ValueError('invalid bundle descriptor')
            if bundle['url'] is not None or not allow_unpublished:
                public_url(bundle['url'])
            if not re.fullmatch(r'[0-9a-f]{64}',bundle['sha256']) or bundle['object_format'] not in {'sha1','sha256'}:
                raise ValueError('invalid bundle digest/format')
            rows=bundle['refs']
            if rows!=sorted(rows,key=lambda r:r['name']) or len({r['name'] for r in rows})!=len(rows) or hashlib.sha256(canonical(rows)).hexdigest()!=bundle['refs_digest']:
                raise ValueError('invalid complete ref manifest')
            for row in rows:
                if set(row)!={'name','object','peeled'} or not row['name'].startswith(('refs/heads/','refs/tags/')) or not HEX.fullmatch(row['object']) or row['peeled'] is not None and not HEX.fullmatch(row['peeled']):
                    raise ValueError('invalid bundle ref')
            if not HEX.fullmatch(source['commit'] or ''):
                raise ValueError('invalid frozen commit')
    return lock


def materialize_sources(lock, destination):
    validate_lock(lock)
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    mapping={}
    for source in lock['sources']:
        if source['kind']!='git':
            continue
        bundle=source['bundle']; asset=destination/(bundle['sha256']+'.bundle')
        fetch(bundle['url'],asset)
        if hashlib.sha256(asset.read_bytes()).hexdigest()!=bundle['sha256']:
            raise ValueError('immutable source asset tampered')
        mirror=destination/(bundle['sha256']+'.git')
        git('init','--bare','--object-format='+bundle['object_format'],mirror)
        git('bundle','verify',asset,cwd=mirror)
        git('fetch',asset,'+refs/*:refs/*',cwd=mirror)
        if refs(mirror)!=bundle['refs'] or git('rev-parse','--show-object-format',cwd=mirror)!=bundle['object_format']:
            raise ValueError('bundle manifest/object format mismatch')
        if git('rev-parse',source['ref']+'^{commit}',cwd=mirror)!=source['commit']:
            raise ValueError('frozen requested ref mismatch')
        if source['tag_object'] and git('rev-parse',source['ref'],cwd=mirror)!=source['tag_object']:
            raise ValueError('frozen tag object mismatch')
        for path in mirror.rglob('*'):
            if path.is_symlink():
                raise ValueError('unexpected mirror symlink')
            path.chmod(0o555 if path.is_dir() else 0o444)
        mirror.chmod(0o555)
        if source['url'] in mapping and mapping[source['url']]!=mirror:
            raise ValueError('conflicting frozen contexts for source URL')
        mapping[source['url']]=mirror
    return mapping


def probe_recipe(recipe_dir, lock, policy, preserve_pkgrel=False):
    """Execute only inside the enrolled Arch container as an unprivileged user."""
    if not Path('/etc/arch-release').is_file() or os.geteuid()==0:
        raise RuntimeError('probe_recipe requires isolated unprivileged Arch runtime')
    from tools.recipe_gate import parse_srcinfo
    original_pkgrel=parse_srcinfo((Path(recipe_dir)/'.SRCINFO').read_text())['pkgrel']
    validate_lock(lock)
    configuration=os.environ.get('GIT_CONFIG_GLOBAL')
    if any(source['kind']=='git' for source in lock['sources']):
        if not configuration or not Path(configuration).is_file():
            raise ValueError('native probe requires root-owned frozen mirror configuration')
        info=Path(configuration).stat()
        if info.st_uid!=0 or info.st_mode&0o222 and not os.statvfs(configuration).f_flag&os.ST_RDONLY:
            raise ValueError('native frozen configuration is writable by recipe')
        entries=git('config','--file',configuration,'--get-regexp',r'^url\..*\.insteadof$',check=False)
        mapping={}
        for row in entries.stdout.splitlines():
            key,url=row.split(None,1)
            target=key.removeprefix('url.').removesuffix('.insteadof')
            if not target.startswith('file:///'):
                raise ValueError('native Git source mapping is not a frozen local mirror')
            mirror=Path(target.removeprefix('file://'))
            readonly=not mirror.stat().st_mode&0o222 or os.statvfs(mirror).f_flag&os.ST_RDONLY
            if mirror.stat().st_uid!=0 or not readonly:
                raise ValueError('native Git mirror is not root-owned readonly')
            mapping[url]=mirror
        for source in lock['sources']:
            if source['kind']=='git':
                mirror=mapping.get(source['url'])
                if mirror is None or refs(mirror)!=source['bundle']['refs'] or git('rev-parse',source['ref']+'^{commit}',cwd=mirror)!=source['commit']:
                    raise ValueError('native mirror context differs from frozen lock')
    vcs_rules=[r for r in policy['automatic']['checksums'] if next(s for s in lock['sources'] if s['id']==r['source_id'])['kind']=='git']
    if vcs_rules:
        import shlex
        generated=subprocess.run(['makepkg','--geninteg'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
        if len(generated.encode())>1048576:
            raise ValueError('native checksum output exceeds bound')
        text=(Path(recipe_dir)/'PKGBUILD').read_text()
        for rule in vcs_rules:
            rows=re.findall(r'^'+re.escape(rule['algorithm']+'sums')+r'=\(([^)]*)\)',generated,re.M|re.S)
            if len(rows)!=1:
                raise ValueError('ambiguous native VCS checksum output')
            values=shlex.split(rows[0])
            value=values[rule['index']]
            expected_length=hashlib.new(rule['algorithm']).digest_size*2
            if not re.fullmatch('[0-9a-f]{'+str(expected_length)+'}',value):
                raise ValueError('invalid native generated VCS checksum')
            source=next(s for s in lock['sources'] if s['id']==rule['source_id'])
            old=source['checksums'][rule['algorithm']]
            if old!=value:
                if text.count(old)!=1:
                    raise ValueError('ambiguous enrolled VCS checksum literal')
                text=text.replace(old,value)
                source['checksums'][rule['algorithm']]=value
        (Path(recipe_dir)/'PKGBUILD').write_text(text)
    subprocess.run(['makepkg','--nobuild','--noconfirm'],cwd=recipe_dir,check=True,stdout=subprocess.PIPE)
    text=subprocess.run(['makepkg','--printsrcinfo'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
    if len(text.encode())>1048576:
        raise ValueError('native metadata exceeds bound')
    metadata=parse_srcinfo(text)
    if preserve_pkgrel and metadata['pkgrel']!=original_pkgrel:
        from tools.update import render_recipe
        render_recipe(Path(recipe_dir),policy,metadata['pkgver'],{source['id']:source['checksums'] for source in lock['sources']},pkgrel=original_pkgrel)
        text=subprocess.run(['makepkg','--printsrcinfo'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
        if len(text.encode())>1048576:
            raise ValueError('native metadata exceeds bound')
        metadata=parse_srcinfo(text)
        if metadata['pkgrel']!=original_pkgrel:
            raise ValueError('native enrollment did not preserve pkgrel')
    runtime_identity=None
    if policy['pkgbase']=='oh-my-pi-vith-git':
        match=re.fullmatch(r'([0-9]+(?:\.[0-9]+){2})\.vith\.r([0-9]+)\.g([0-9a-f]{12})',metadata['pkgver'])
        if not match:
            raise ValueError('unexpected native fork identity')
        source=next(s for s in lock['sources'] if s['id']=='omp-git')
        if source['commit'][:12]!=match[3]:
            raise ValueError('native fork SHA differs from frozen commit')
        runtime_identity=f'{match[1]}+vith-fork.{match[2]}.{match[3]}'
    return {'schema':1,'version':metadata['version'],'pkgver':metadata['pkgver'],'pkgrel':metadata['pkgrel'],'srcinfo':text,'checksums':{s['id']:s['checksums'] for s in lock['sources']},'sources':lock['sources'],'runtime_identity':runtime_identity}
