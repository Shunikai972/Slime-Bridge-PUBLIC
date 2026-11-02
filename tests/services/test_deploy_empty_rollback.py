"""An existing empty artifact is a file, not an absent preimage, during rollback and crash recovery."""
import pytest

from bridge.deploy.executor import DirectoryFS, MemoryFS
from bridge.sim.faults import Fault, FaultPlan
from services.test_deploy import Deployment, unit


@pytest.mark.parametrize('backend',['memory','directory'])
@pytest.mark.parametrize('action,fault',[
    ('update','deploy.postcondition'),
    ('disable','deploy.postcondition'),
    ('update','deploy.crash_after_step'),
    ('update','deploy.crash_before_write'),
])
def test_empty_preimage_is_restored_and_recovery_is_idempotent(tmp_path,backend,action,fault):
    fs=MemoryFS() if backend=='memory' else DirectoryFS(tmp_path/'target')
    deployment=Deployment(tmp_path,fs=fs)
    try:
        old=unit('empty-fixture',files={'fixture/empty.bin':''})
        assert deployment.apply([{'key':old['key'],'state':'ENABLED','unit':old}])['state']=='COMMITTED'
        assert fs.read('live/fixture/empty.bin')==b''
        if action=='update':
            new=unit('empty-fixture',(2,0,0),files={'fixture/empty.bin':'changed'})
            desired=[{'key':new['key'],'state':'ENABLED','unit':new}]
        else:
            desired=[{'key':old['key'],'state':'DISABLED'}]
        deployment.executor.faults=FaultPlan([Fault(fault)])
        result=deployment.apply(desired)
        assert deployment.executor.faults.fired_points()==[fault]
        if fault.startswith('deploy.crash_'):
            assert result['state']=='APPLYING'
            assert deployment.executor.recover()=={result['txn_id']:'ROLLED_BACK'}
        else:
            assert result['state']=='ROLLED_BACK',result
        assert fs.read('live/fixture/empty.bin')==b''
        assert deployment.executor.recover()=={}
        assert fs.read('live/fixture/empty.bin')==b''
    finally:
        deployment.journal.close()
