import os

import pandas as pd
import pytest

from lumen.sources.base import DerivedSource

from .utils import (
    source_clear_cache_get_query, source_clear_cache_get_schema,
    source_get_cache_no_query, source_get_tables, source_table_cache_key,
)


@pytest.fixture
def original(make_filesource):
    root = os.path.dirname(__file__)
    return make_filesource(root)


@pytest.fixture
def expected_table():
    df = pd._testing.makeMixedDataFrame()
    return df.iloc[:3]


@pytest.fixture
def expected_schema():
    schema = {
        'A': {'inclusiveMaximum': 2.0, 'inclusiveMinimum': 0.0, 'type': 'number'},
        'B': {'inclusiveMaximum': 1.0, 'inclusiveMinimum': 0.0, 'type': 'number'},
        'C': {'enum': ['foo1', 'foo2', 'foo3'], 'type': 'string'},
        'D': {
            'format': 'datetime',
            'type': 'string',
            'inclusiveMaximum': '2009-01-05T00:00:00',
            'inclusiveMinimum': '2009-01-01T00:00:00'
        }
    }
    return schema


@pytest.fixture
def mirror_mode_spec():
    spec = {
        'type': 'derived',
        'source': 'original',
    }
    return spec


@pytest.fixture
def tables_mode_spec():
    spec = {
        'type': 'derived',
        'tables': {'derived': {'source': 'original', 'table': 'test'}}
    }
    return spec


@pytest.fixture
def tables_mode_derived(original, tables_mode_spec):
    return DerivedSource.from_spec(tables_mode_spec)


@pytest.fixture
def source_tables():
    return {'derived': pd._testing.makeMixedDataFrame()}


def test_derived_source_resolve_module_type():
    assert DerivedSource._get_type('lumen.sources.base.DerivedSource') is DerivedSource
    assert DerivedSource.source_type == 'derived'


def test_derived_mirror_source(original, mirror_mode_spec):
    derived = DerivedSource.from_spec(mirror_mode_spec)
    assert derived.get_tables() == original.get_tables()
    assert derived.get_schema() == original.get_schema()
    for table in original.get_tables():
        pd.testing.assert_frame_equal(derived.get(table), original.get(table))


@pytest.mark.parametrize("additional_spec", [
    ('transforms', [{'type': 'iloc', 'end': 3}]),
    ('filters', [{'type': 'constant', 'field': 'A', 'value': (0, 2)}]),
])
def test_derived_mirror_source_apply(
        original, mirror_mode_spec, additional_spec, expected_table, expected_schema
):
    spec_key, spec_value = additional_spec
    mirror_mode_spec[spec_key] = spec_value
    derived = DerivedSource.from_spec(mirror_mode_spec)
    assert derived.get_tables() == original.get_tables()
    assert derived.get_schema('test') == expected_schema
    pd.testing.assert_frame_equal(derived.get('test'), expected_table)


@pytest.mark.parametrize("additional_spec", [
    ('transforms', [{'type': 'iloc', 'end': 3}]),
    ('filters', [{'type': 'constant', 'field': 'A', 'value': (0, 2)}]),
])
def test_derived_tables_source_apply(
        original, tables_mode_spec, additional_spec, expected_table, expected_schema
):
    spec_key, spec_value = additional_spec
    tables_mode_spec['tables']['derived'][spec_key] = spec_value
    derived = DerivedSource.from_spec(tables_mode_spec)
    assert derived.get_tables() == ['derived']
    pd.testing.assert_frame_equal(derived.get('derived'), expected_table)
    assert derived.get_schema('derived') == expected_schema


def test_derived_tables_source(tables_mode_derived, source_tables):
    assert source_get_tables(tables_mode_derived, source_tables)


def test_derived_cache_key(tables_mode_derived):
    table = 'derived'
    assert source_table_cache_key(tables_mode_derived, table)


def test_derived_get_cache_no_query(tables_mode_derived, source_tables):
    for table in source_tables:
        expected_table = source_tables[table]
        source_get_cache_no_query(tables_mode_derived, table, expected_table)


def test_derived_clear_cache_get_query(tables_mode_derived):
    assert source_clear_cache_get_query(tables_mode_derived, table='derived')


def test_derived_clear_cache_get_schema(tables_mode_derived):
    assert source_clear_cache_get_schema(tables_mode_derived, table='derived')
