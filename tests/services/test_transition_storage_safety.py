"""Transition storage initialization owns its SQLite connection, including failed constructors."""
import sqlite3

import pytest

from bridge.transition.machine import TransitionError, TransitionMachine


@pytest.mark.parametrize('failure',['malformed-meta','pragma'])
def test_failed_constructor_closes_the_connection_with_traceback_retained(tmp_path,monkeypatch,failure):
    path=tmp_path/'transition.sqlite3'
    if failure=='malformed-meta':
        connection=sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE meta (wrong_column TEXT)')
            connection.commit()
        finally:
            connection.close()
    connect=sqlite3.connect
    opened=[]
    class PragmaFailure(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql=='PRAGMA synchronous=FULL':
                raise sqlite3.OperationalError('injected initialization pragma failure')
            return super().execute(sql,*args,**kwargs)
    def record(*args,**kwargs):
        if failure=='pragma':
            kwargs['factory']=PragmaFailure
        connection=connect(*args,**kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr('bridge.transition.machine.sqlite3.connect',record)
    try:
        with pytest.raises(sqlite3.OperationalError) as error:
            TransitionMachine(path,trust={})
        assert error.value.__traceback__ is not None
        assert len(opened)==1
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):
            opened[0].execute('SELECT 1')
        # Exercise the real Windows sharing constraint while the traceback is still alive.
        renamed=path.with_suffix('.renamed')
        path.replace(renamed)
        assert renamed.is_file()
        assert error.value.__traceback__ is not None
    finally:
        for connection in opened:
            connection.close()


@pytest.mark.parametrize('version',['99','0','invalid','01',''])
def test_unsupported_schema_is_refused_without_mutating_the_database(tmp_path,version):
    path=tmp_path/'unsupported.sqlite3'
    connection=sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        connection.execute("INSERT INTO meta VALUES ('schema_version',?)",(version,))
        connection.execute('CREATE TABLE future_data (value BLOB NOT NULL)')
        connection.execute('INSERT INTO future_data VALUES (?)',(b'preserve opaque future data',))
        connection.commit()
    finally:
        connection.close()
    before=path.read_bytes()
    machine=None
    try:
        with pytest.raises(TransitionError) as error:
            machine=TransitionMachine(path,trust={})
        assert error.value.code=='SCHEMA_UNSUPPORTED'
        assert path.read_bytes()==before
        assert error.value.__traceback__ is not None
        renamed=path.with_suffix('.renamed')
        path.replace(renamed)
        assert renamed.read_bytes()==before
    finally:
        if machine is not None:
            machine.close()


@pytest.mark.parametrize('existing_meta',[False,True])
def test_current_schema_initializes_and_reopens_without_losing_metadata(tmp_path,existing_meta):
    path=tmp_path/'current.sqlite3'
    if existing_meta:
        connection=sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            connection.execute("INSERT INTO meta VALUES ('owner-note','keep')")
            connection.commit()
        finally:
            connection.close()
    for _ in range(2):
        machine=TransitionMachine(path,trust={})
        try:
            assert machine._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='1'
            assert machine._db.execute('SELECT * FROM transitions').fetchall()==[]
            assert machine._db.execute('SELECT * FROM events').fetchall()==[]
            if existing_meta:
                assert machine._db.execute("SELECT value FROM meta WHERE key='owner-note'").fetchone()[0]=='keep'
        finally:
            machine.close()
