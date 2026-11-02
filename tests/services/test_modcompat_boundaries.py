"""Synthetic manifests, template files and pure plans; no mod/native deployment."""
import hashlib
import os

import pytest

from bridge.app import mods

from bridge.core.paths import safe_relative
from bridge.deploy.planner import empty_inventory, plan_deploy
from bridge.modcompat import analyzer as A, evidence as E, manifest as M, skeleton as K


def config_mod(mod_id='fixture.consumer', dependencies=(), version=(1, 0, 0), rel=None):
    files = [{'path': rel or f'Mods/{mod_id}/settings.json',
              'sha256': hashlib.sha256(mod_id.encode()).hexdigest(), 'size': 8}]
    manifest = M.make_manifest(id=mod_id, native_id=mod_id, name='Synthetic config',
        version_text='.'.join(map(str, version)), version_parts=list(version), realm='SR2', loader='MELONLOADER',
        source=M.provider_source('fixture.synthetic', synthetic=True), files=files, dependencies=list(dependencies))
    evidence = {'schema': E.EVIDENCE_SCHEMA, 'schema_version': 1, 'mod_id': mod_id,
        'manifest_id': manifest['manifest_id'], 'extractor': {'id': 'fixture.synthetic', 'version': '1.0.0'},
        'synthetic': True, 'code': {'assemblies': []}, 'references': [],
        'harmony': {'patch_all_calls': 0, 'patches': []},
        'native': {'pinvoke_modules': [], 'detour_indicators': [], 'function_pointer_hooks': False},
        'content': {'asset_bundles': [], 'content_files': [], 'content_ids': []}, 'config_files': files,
        'reflection': {'type_lookup_by_name': False, 'non_public_access': False, 'dynamic_invoke': False, 'emit': False},
        'il2cpp': {'interop_references': False, 'type_injection': False, 'il2cpp_type_references': 0}}
    return manifest, evidence


class SyntheticJournal:
    def __init__(self, inventory=None):
        self.value = inventory or empty_inventory('SR2')
    def inventory(self, realm):
        assert realm == 'SR2'
        return self.value
    def history(self):
        return {}


@pytest.mark.parametrize('kind', ['ASSEMBLY', 'MOD'])
def test_unchecked_required_dependency_is_not_deployable(kind):
    manifest, evidence = config_mod(dependencies=[{'id': 'fixture.required', 'kind': kind,
                                                   'constraint': '>=2.0.0', 'optional': False}])
    assert M.validate_manifest(manifest) == []
    assert E.validate_evidence(evidence) == []
    assert E.bind(manifest, evidence) == []
    report = A.analyze(manifest, evidence)
    unit, errors = A.to_deploy_unit(manifest, report)
    assert not errors
    assert unit['status'] != 'READY'
    assert not plan_deploy(empty_inventory('SR2'), [{'key': unit['key'], 'unit': unit, 'state': 'ENABLED'}])['executable']
    row = mods.rows([{'manifest': manifest, 'evidence': evidence, 'diagnostics': []}])[0]
    assert not mods.enable_plan(SyntheticJournal(), manifest, row['report'])['executable']


def test_resolved_catalog_report_does_not_authorize_wrong_installed_version():
    required, required_ev = config_mod('fixture.required', version=(2, 0, 0))
    old, old_ev = config_mod('fixture.required', version=(1, 0, 0))
    manifest, evidence = config_mod(dependencies=[{'id': 'fixture.required', 'kind': 'MOD',
                                                   'constraint': '>=2.0.0', 'optional': False}])
    report = A.analyze(manifest, evidence, available=[required])
    assert A.unit_status(report) == 'READY'
    old_unit, errors = A.to_deploy_unit(old, A.analyze(old, old_ev))
    assert not errors
    inventory = empty_inventory('SR2')
    inventory['units'][old_unit['key']] = {'unit': old_unit, 'state': 'ENABLED'}
    assert not mods.enable_plan(SyntheticJournal(inventory), manifest, report)['executable']
    good_unit, errors = A.to_deploy_unit(required, A.analyze(required, required_ev))
    assert not errors
    inventory['units'][good_unit['key']] = {'unit': good_unit, 'state': 'ENABLED'}
    assert mods.enable_plan(SyntheticJournal(inventory), manifest, report)['executable']


@pytest.mark.parametrize('path', ['Mods/Alias.dll.', 'Mods./Alias.dll', 'Mods/CON', 'Mods/Con.txt',
                                 'Mods/AUX.json', 'Mods/COM1.dll', 'LPT9/settings.json'])
def test_win32_alias_segments_are_not_safe_artifact_paths(path):
    assert safe_relative(path) is False
    manifest, evidence = config_mod(rel=path)
    assert M.validate_manifest(manifest)
    assert E.validate_evidence(evidence)
    report = A.analyze(manifest, evidence)
    assert A.unit_status(report) != 'READY'


@pytest.mark.skipif(os.name != 'nt', reason='real Win32 trailing-dot alias')
def test_real_trailing_dot_alias_cannot_bind_as_config_or_be_deployed(tmp_path):
    canonical = tmp_path / 'Alias.dll'
    canonical.write_bytes(b'inert synthetic bytes; never executable')
    alias = tmp_path / 'Alias.dll.'
    assert os.path.samefile(canonical, alias)
    assert canonical.read_bytes() == alias.read_bytes()
    manifest, evidence = config_mod(rel='Mods/Alias.dll.')
    report = A.analyze(manifest, evidence)
    unit, diagnostics = A.to_deploy_unit(manifest, report)
    assert unit is None
    assert diagnostics
    assert canonical.read_bytes() == b'inert synthetic bytes; never executable'


def template_report(mod_id):
    manifest, evidence = config_mod(mod_id)
    return A.analyze(manifest, evidence, target_realm='SR1')


def snapshot_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_lossy_mod_slug_collision_never_overwrites_first_adapter(tmp_path):
    first = K.generate_adapter_skeleton(template_report('fixture.a_b'), out_root=tmp_path)
    assert first['status'] == 'WRITTEN'
    cs = next((tmp_path / first['directory']).glob('*.cs'))
    cs.write_bytes(cs.read_bytes() + b'\n// synthetic developer-owned change\n')
    before = snapshot_files(tmp_path)
    second = K.generate_adapter_skeleton(template_report('fixture.a-b'), out_root=tmp_path)
    assert second['status'] == 'WRITTEN'
    assert second['directory'] != first['directory']
    assert all((tmp_path / rel).read_bytes() == data for rel, data in before.items())


def test_regeneration_refuses_modified_source_before_any_write(tmp_path):
    report = template_report('fixture.owner')
    first = K.generate_adapter_skeleton(report, out_root=tmp_path)
    assert first['status'] == 'WRITTEN'
    cs = next((tmp_path / first['directory']).glob('*.cs'))
    cs.write_bytes(cs.read_bytes() + b'\n// synthetic implementation\n')
    before = snapshot_files(tmp_path)
    again = K.generate_adapter_skeleton(report, out_root=tmp_path)
    assert again['status'] == 'REFUSED'
    assert snapshot_files(tmp_path) == before


def test_identical_same_owner_generation_remains_idempotent(tmp_path):
    report = template_report('fixture.unchanged')
    first = K.generate_adapter_skeleton(report, out_root=tmp_path)
    before = snapshot_files(tmp_path)
    again = K.generate_adapter_skeleton(report, out_root=tmp_path)
    assert first['status'] == again['status'] == 'WRITTEN'
    assert first['files'] == again['files']
    assert snapshot_files(tmp_path) == before
