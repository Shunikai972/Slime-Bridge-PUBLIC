"""Read-only attestation must refuse linked roots, ancestors and artifact components."""
import hashlib

import pytest

from bridge.core.paths import resolve_confined
from bridge.packages.package import verify_artifact_store
from portability import directory_link, file_symlink


DATA=b'synthetic external artifact'


def package(path):
    return {'artifacts':[{'path':path,'sha256':hashlib.sha256(DATA).hexdigest()}]}


@pytest.mark.parametrize('position',['nested','root','ancestor','lexical-parent','missing-tail'])
def test_real_directory_redirect_is_refused_without_reading_it(tmp_path,position,monkeypatch):
    outside=tmp_path/'outside target'
    (outside/'child').mkdir(parents=True)
    sentinel=outside/'child/model.json'
    sentinel.write_bytes(DATA)
    local=tmp_path/'owned package'
    local.mkdir()
    link=local/'redirect'
    with directory_link(link,outside):
        if position=='nested':
            root,relative=local,'redirect/child/model.json'
        elif position=='root':
            root,relative=link,'child/model.json'
        elif position=='ancestor':
            root,relative=link/'child','model.json'
        elif position=='lexical-parent':
            root,relative=link/'child'/'..','child/model.json'
        else:
            root,relative=local,'redirect/missing/model.json'
        assert resolve_confined(root,relative) is None
        def forbidden_read(_path):
            pytest.fail('artifact hashing must not run on a redirected path')
        monkeypatch.setattr('bridge.packages.package.hash_file',forbidden_read)
        problems=verify_artifact_store(package(relative),root)
        assert len(problems)==1
        assert problems[0]['code']=='ARTIFACT_UNREADABLE'
        assert sentinel.read_bytes()==DATA
    assert sorted(p.relative_to(outside).as_posix() for p in outside.rglob('*'))==['child','child/model.json']


def test_normal_artifact_and_missing_tail_remain_usable(tmp_path):
    root=tmp_path/'owned'
    root.mkdir()
    (root/'model.json').write_bytes(DATA)
    assert resolve_confined(root,'model.json')==root/'model.json'
    assert verify_artifact_store(package('model.json'),root)==[]
    assert resolve_confined(root,'missing/model.json')==root/'missing/model.json'


def test_file_symlink_is_refused_without_changing_target(tmp_path):
    outside=tmp_path/'outside.json'
    outside.write_bytes(DATA)
    root=tmp_path/'owned'
    root.mkdir()
    file_symlink(root/'model.json',outside)
    assert resolve_confined(root,'model.json') is None
    assert verify_artifact_store(package('model.json'),root)[0]['code']=='ARTIFACT_UNREADABLE'
    assert outside.read_bytes()==DATA
