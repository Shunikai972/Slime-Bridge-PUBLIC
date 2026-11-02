"""An atomic writer must not turn a valid final filename into an invalid, much longer temporary sibling."""
import builtins
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bridge.cache import fs


def test_atomic_write_keeps_the_temporary_name_independent_of_the_target_name(tmp_path):
    root=fs.confined_root(tmp_path)
    length=min(220,245-len(str(root))-1) if os.name=='nt' else 220
    assert length>=8, 'Test root leaves no room for a valid ordinary Windows filename'
    target=root/('f'*(length-5)+'.json')
    target.write_bytes(b'previous')  # prove the final path itself is supported by this filesystem
    fs.atomic_write(root,target,b'new contents')
    assert target.read_bytes()==b'new contents'
    assert sorted(p.name for p in root.iterdir())==[target.name]


def test_atomic_write_does_not_delete_a_temporary_name_it_failed_to_create(tmp_path):
    root=fs.confined_root(tmp_path)
    target=root/'result.json'
    target.write_bytes(b'previous')
    planted=[]
    real_open=builtins.open

    def collide(path,mode,*args,**kwargs):
        assert mode=='xb'
        with real_open(path,'xb') as other:
            other.write(b'unowned temporary file')
        planted.append(Path(path))
        raise FileExistsError(str(path))

    with patch.object(fs,'open',side_effect=collide,create=True),pytest.raises(FileExistsError):
        fs.atomic_write(root,target,b'new contents')
    assert target.read_bytes()==b'previous'
    assert len(planted)==1 and planted[0].read_bytes()==b'unowned temporary file'
