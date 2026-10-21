"""Read an enum's names and values from a managed assembly without loading or running it.

The SR1 world stores ``Identifiable.Id`` as a bare integer; the names live only in ``Assembly-CSharp.dll``. This reads
the .NET metadata tables (``dnfile``, pure Python): nothing is executed and the file is opened read-only.
"""
from __future__ import annotations

from pathlib import Path


def read_nested_enum(assembly, enclosing, name):
    """Return ``{value: member_name}`` for the nested enum ``enclosing.name`` (for example ``Identifiable.Id``)."""
    try:
        import dnfile
    except ImportError as exc:                                   # pragma: no cover - dependency hint
        raise RuntimeError('reading managed enums needs the "dnfile" package (py -3.12 -m pip install dnfile)') from exc
    path = Path(assembly)
    if not path.is_file():
        raise ValueError(f'assembly missing: {path.name}')
    pe = dnfile.dnPE(str(path))
    tables = pe.net.mdtables
    types, fields = list(tables.TypeDef.rows), list(tables.Field.rows)
    nested = {r.NestedClass.row_index: r.EnclosingClass.row_index for r in tables.NestedClass.rows}
    found = [i for i, row in enumerate(types, 1)
             if str(row.TypeName) == name and i in nested and str(types[nested[i] - 1].TypeName) == enclosing]
    if len(found) != 1:
        raise ValueError(f'enum {enclosing}.{name} not found uniquely in {path.name}')
    constants = {}
    for constant in tables.Constant.rows:
        parent = constant.Parent
        if parent.table is not None and parent.table.name == 'Field':
            constants[parent.row_index] = int.from_bytes(constant.Value.value, 'little', signed=True)
    members = {}
    for index in types[found[0] - 1].FieldList:
        field_name = str(fields[index.row_index - 1].Name)
        if field_name == 'value__' or index.row_index not in constants:
            continue
        members[constants[index.row_index]] = field_name
    if not members:
        raise ValueError(f'enum {enclosing}.{name} has no members')
    return members
