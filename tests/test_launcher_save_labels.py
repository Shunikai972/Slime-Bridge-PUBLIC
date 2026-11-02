"""Read-only save enumeration keeps arbitrary valid group names even when their prefix is not a date."""
from pathlib import Path

import pytest

from apps.launcher import savelist


@pytest.mark.parametrize('group', ['20261301000000_custom','20260230000000_custom','00000101000000_custom','20261101250000_custom'])
def test_invalid_calendar_prefix_is_a_plain_save_group_name(group):
    assert savelist._label(group)==group


def test_malformed_name_does_not_hide_other_saves_or_write_their_files(tmp_path):
    groups=['20261301000000_custom','20261101123000_Ranch','ordinary_ranch']
    files={tmp_path/(group+'_1.sav'):b'synthetic save contents '+group.encode()for group in groups}
    for path,data in files.items():
        path.write_bytes(data)
    listed=savelist.list_groups('SR1',roots={'SR1':tmp_path})
    by_group={row['group']:row for row in listed}
    assert set(by_group)==set(groups)
    assert by_group[groups[0]]['label']==groups[0]
    assert by_group[groups[1]]['label']=='Ranch (2026-11-01 12:30)'
    assert by_group[groups[2]]['label']==groups[2]
    assert {path:path.read_bytes()for path in files}==files
