"""The model-store sibling of cache publication must preserve its active slot on replacement failure."""
import hashlib
import io
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest

from apps.launcher import models
from portability import directory_link


def archive_bytes(names=None):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,data in names or [('rad_cotton.glb',b'synthetic model')]:
            archive.writestr(name,data)
    return buffer.getvalue()


def fixture(tmp_path):
    data=archive_bytes()
    entry={'id':'rad_cotton','version':'1','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
           'url':'https://fixture.invalid/model.zip'}
    store=models.ModelStore(tmp_path/'models',opener=lambda *_a,**_k:io.BytesIO(data))
    previous=store.root/entry['id']
    previous.mkdir(parents=True)
    (previous/'old.glb').write_bytes(b'old model')
    return store,entry,previous


def test_model_publication_failure_restores_the_previous_active_slot(tmp_path):
    store,entry,previous=fixture(tmp_path)
    replace=models.os.replace
    calls=[]

    def fail_publication(source,target):
        calls.append((source,target))
        if len(calls)==2:
            raise PermissionError('synthetic model publication failure')
        return replace(source,target)

    with patch.object(models.os,'replace',side_effect=fail_publication),pytest.raises(PermissionError):
        store.download(entry)
    assert (previous/'old.glb').read_bytes()==b'old model'
    assert list(store.root.iterdir())==[previous]


def test_model_removal_refuses_a_junction_root(tmp_path):
    outside=tmp_path/'outside'
    model=outside/'rad_cotton'
    model.mkdir(parents=True)
    marker=model/'user.glb'
    marker.write_bytes(b'unowned')
    with directory_link(tmp_path/'redirected',outside) as link:
        with pytest.raises((models.ModelError,ValueError)):
            models.ModelStore(link).remove('rad_cotton')
        assert marker.read_bytes()==b'unowned'


def test_model_archive_case_collision_is_rejected_before_any_files_are_written(tmp_path):
    archive=tmp_path/'models.zip'
    archive.write_bytes(archive_bytes([('model.glb',b'a'),('MODEL.glb',b'b')]))
    destination=tmp_path/'unpacked'
    with pytest.raises(models.ModelError):
        models._safe_unzip(archive,destination)
    assert not list(destination.rglob('*'))
