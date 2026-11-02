"""Malformed external package data must be rejected, not crash its validators."""
import copy

import pytest

from bridge.core import diagnostics
from bridge.packages import package
from services.test_packages import package_for


@pytest.mark.parametrize('lineage', [None, [], [1], 'broken', 7, {'canonical_id': []}])
def test_invalid_lineage_returns_schema_diagnostic(lineage):
    problems = package.validate_package({'lineage': lineage})
    assert {p['code'] for p in problems} == {'PACKAGE_SCHEMA_INVALID'}
    assert all(not diagnostics.validate(p) for p in problems)
    assert package.installable({'lineage': lineage}) is False


@pytest.mark.parametrize('value', [None, [], 'broken', 7])
def test_non_object_package_is_not_installable(value):
    assert package.validate_package(value)
    assert package.installable(value) is False


@pytest.mark.parametrize('operations', [None, 'broken', 7, [None], ['broken'], [7]])
def test_malformed_registration_operations_return_diagnostic(operations):
    manifest = copy.deepcopy(package_for('slime.azure'))
    manifest['registration_plan']['operations'] = operations
    package._seal(manifest)
    problems = package.validate_package(manifest)
    assert problems
    assert all(not diagnostics.validate(p) for p in problems)
    assert package.installable(manifest) is False


def test_valid_package_keeps_its_canonical_diagnostic_context():
    manifest = copy.deepcopy(package_for('slime.azure'))
    manifest['package_id'] = '0' * 64
    problems = package.validate_package(manifest)
    assert {p['code'] for p in problems} == {'PACKAGE_ID_MISMATCH'}
    assert problems[0]['content_id'] == 'slime.azure'
