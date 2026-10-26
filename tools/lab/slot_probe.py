"""Validate read-only SR2 occupancy evidence; deliberately never select/create a slot."""
from __future__ import annotations


def validate_slot_report(report: dict) -> dict:
    if (not isinstance(report, dict) or type(report.get('schema_version')) is not int or
            report['schema_version'] != 1 or report.get('read_only') is not True or
            report.get('permitted_slot_range_verified') is not False or
            report.get('creation_authorized') is not False):
        raise ValueError('not a read-only, range-unverified slot probe')
    for key in ('save_slot_count_observed', 'raw_save_count'):
        if type(report.get(key)) is not int or not 0 <= report[key] <= 100_000:
            raise ValueError('invalid diagnostic count')
    rows, occupied, groups, errors = (report.get(key) for key in
                                     ('summaries', 'occupied_slot_indices', 'group_names', 'errors'))
    if (not isinstance(rows, list) or not isinstance(occupied, list) or
            not isinstance(groups, list) or not isinstance(errors, list) or
            type(report.get('occupancy_complete')) is not bool):
        raise ValueError('invalid diagnostic lists or completeness')
    if (any(not isinstance(value, str) for value in groups + errors) or
            any(type(value) is not int or value < 0 for value in occupied)):
        raise ValueError('invalid diagnostic lists or completeness')
    names, valid_slots, native_groups = set(), set(), set()
    unknown = bool(errors) or len(rows) != report['raw_save_count']
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('slot_index')) is not int or
                type(row.get('invalid')) is not bool or
                any(not isinstance(row.get(k), str) or not row[k] for k in
                    ('raw_name', 'game_name', 'save_name'))):
            raise ValueError('malformed native summary diagnostic')
        if row['raw_name'] in names:
            raise ValueError('duplicate raw native save')
        names.add(row['raw_name'])
        native_groups.add(row['game_name'])
        if row['invalid'] or row['slot_index'] < 0:
            unknown = True
        else:
            valid_slots.add(row['slot_index'])
    if occupied != sorted(valid_slots):
        raise ValueError('occupied indices do not match summaries')
    if report['occupancy_complete'] and (unknown or native_groups != set(groups)):
        raise ValueError('complete occupancy claim contradicts raw/group observations')
    return {**report, 'status': 'OCCUPANCY_OBSERVED' if report['occupancy_complete'] and not unknown
            else 'BLOCKED_UNKNOWN', 'creation_authorized': False}
