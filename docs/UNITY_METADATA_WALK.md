# Read-only Unity metadata graph slice

`bridge.conversion.unity_metadata.read_unity_metadata` returns a plain
`bridge-content-source` v1 manifest consumable by the **unchanged** `ManifestSource`
and `AssetDependencyProvider` boundary. It never writes files or loads Unity/game code.

```python
from bridge.conversion.unity_metadata import read_unity_metadata
from bridge.conversion.manifest import ManifestSource
from bridge.content.discovery import discover
from bridge.content.graph import NodeKind

# Supply an explicitly authorized, trusted, immutable export Assets directory.
# This example GUID/fileID is synthetic, not a species/asset mapping.
data = read_unity_metadata(
    authorized_export_root, realm='SR1',
    roots={'Caller.Root': 'unity:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1'},
)
source = ManifestSource(data)
slice_result = discover(source, [source.definition_root({'native_ref': 'Caller.Root'})],
                        root_kind=NodeKind.UNKNOWN)
```

## Evidence and scope

- Export completeness remains **PARTIAL**, build/runtime equivalence **UNVERIFIED**.
  The `UNVERIFIED:unity-metadata-index:<SHA256>` fingerprint identifies an export
  sidecar index, **not** an executable build. Embedded registry entries are empty;
  its traversal is PARTIAL with no expected count. No runtime-anchored evidence,
  species selection, material/behaviour profile, neutral IR or conversion is produced.
- Explicit roots use canonical lowercase GUIDs and nonzero signed decimal fileIDs.
  Source tokens are validated, not repaired. Descriptors retain GUID/fileID, actual
  Unity class tag/type name, stripped flag, relative paths and source/sidecar SHA256.
  Their neutral node kind stays `unknown`; reference `type` tokens are not type maps.
- The GUID index reads `.meta` sidecars; reachable serialized files index all object
  anchors before following references. All recognized PPtr fields are treated alike
  (not just prefab/material fields). Forward references, self references, external
  references and cycles terminate through identity-based deduplication.
- Flat flow/block PPtr mappings and flow/block sequences are recognized without a
  YAML deserializer. Quoted, plain-text and block-scalar strings/comments do not
  become edges. Malformed, duplicate-key or unsupported pointer mappings retain
  token hashes and unresolved surrogate edges; tokens over 256 characters are
  digest-only. Unknown object classes are retained, not inferred.
- `fileID: 0` records are NULL with no edge; missing nonzero targets remain edges
  whose descriptors are absent. Root/reference/asset observations explain missing
  GUIDs/fileIDs/assets, opaque files/directories, read/size failures and unverified
  reserved/builtin GUIDs. Reserved targets never become resolved export objects.
  No importer-derived fileID, binary subasset, alias or builtin identity is invented.
- Defaults: at most 10,000 sidecars, 8 MiB per file, 20,000 walked identities and
  20,000 object anchors per serialized file. Index/object/walk limit violations and
  duplicate GUID/anchor identities fail closed with `ValueError`; an oversized
  reachable asset remains unresolved. A returned slice is never COMPLETE.
- Symlink/reparse directories are not traversed; linked/outside-root assets are not
  read. **This is not an adversarial filesystem sandbox**: checks precede opens,
  so callers must keep the authorized export immutable for the duration of a read.
  Binary/unsupported asset contents are represented only by hashes/status, not bytes.

## Verification

Synthetic fixtures only: `py -3.12 -m unittest discover -s tests/conversion -p test_unity_metadata.py -v`.
No real export, model folder, game, loader, registry/HKCU or save operation is required.

Unity 2021.3 primary references consulted (not source-specific build evidence):

- https://docs.unity3d.com/2021.3/Documentation/Manual/FormatDescription.html — separate
  object documents, Unity class tags and file-local object IDs.
- https://docs.unity3d.com/2021.3/Documentation/Manual/YAMLSceneExample.html — serialized
  reference maps, zero fileIDs and reserved GUID examples.
- https://docs.unity3d.com/2021.3/Documentation/Manual/AssetMetadata.html — `.meta`
  asset identities and corresponding sidecars, including folder metadata.
