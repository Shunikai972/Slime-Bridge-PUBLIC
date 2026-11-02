"""Publication failures and static junctions must not destroy the previous active cache."""
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.launcher import extract
from portability import directory_link


def setup_cache(root):
    output=root/'export'
    source=output/'bridge-cache'/'SR2-to-SR1'/'slime.fixture'
    source.mkdir(parents=True)
    (source/'cache-manifest.json').write_text('{}',encoding='utf8')
    (source/'model.json').write_bytes(b'new cache')
    destination=root/'installed'
    previous=destination/'sr2-to-sr1'/'slime.fixture'
    previous.mkdir(parents=True)
    (previous/'model.json').write_bytes(b'old cache')
    return output,source,destination,previous


@pytest.mark.parametrize('failure_at',[1,2])
def test_publication_failure_retains_or_restores_the_previous_cache(tmp_path,failure_at):
    output,_,destination,previous=setup_cache(tmp_path)
    replace=extract.os.replace
    calls=[]

    def fail_one(source,target):
        calls.append((str(source),str(target)))
        if len(calls)==failure_at:
            raise PermissionError('synthetic publication failure')
        return replace(source,target)

    with patch.object(extract.os,'replace',side_effect=fail_one),pytest.raises(PermissionError):
        extract.install_cache(output,destination)
    assert (previous/'model.json').read_bytes()==b'old cache'
    assert list(previous.parent.iterdir())==[previous]


def test_complete_cache_replaces_previous_and_preserves_unowned_temporary_names(tmp_path):
    output,source,destination,previous=setup_cache(tmp_path)
    unowned=previous.parent/'.slime.fixture.tmp'
    unowned.mkdir()
    (unowned/'retained.txt').write_bytes(b'user file')
    result=extract.install_cache(output,destination)
    assert result['copied']==1
    assert (previous/'model.json').read_bytes()==b'new cache'
    assert (source/'model.json').read_bytes()==b'new cache'
    assert (unowned/'retained.txt').read_bytes()==b'user file'
    assert sorted(p.name for p in previous.parent.iterdir())==['.slime.fixture.tmp','slime.fixture']


@pytest.mark.parametrize('linked',['source','target','nested'])
def test_static_junctions_are_refused_before_touching_an_external_tree(tmp_path,linked):
    output,source,destination,previous=setup_cache(tmp_path)
    outside=tmp_path/'outside'
    outside.mkdir()
    marker=outside/'user.txt'
    marker.write_bytes(b'not owned by installer')
    if linked=='source':
        link=source.parent/'slime.link'
    elif linked=='target':
        link=previous.parent/'slime.link'
        extra=source.parent/'slime.link'
        extra.mkdir()
        (extra/'cache-manifest.json').write_text('{}',encoding='utf8')
    else:
        link=source/'nested'
    with directory_link(link,outside):
        with pytest.raises(ValueError):
            extract.install_cache(output,destination)
        assert marker.read_bytes()==b'not owned by installer'
        assert list(outside.iterdir())==[marker]
        assert (previous/'model.json').read_bytes()==b'old cache'
