"""A realm-switch completion must bind to the whole durable BEGIN intent, not only its id."""
import pytest

from bridge.multiplayer.persistence import ServerStore
from services.test_multiplayer import World, tid


@pytest.mark.parametrize('phase',['COMMIT','ABORT'])
@pytest.mark.parametrize('mutation',['reverse-realms','portal'])
@pytest.mark.parametrize('replace_session',[False,True])
def test_changed_completion_keeps_the_pending_intent(tmp_path,phase,mutation,replace_session):
    store=ServerStore(tmp_path/'server.sqlite3')
    try:
        world=World(store=store)
        client=world.join('alice','SR1')
        intent={'transition_id':tid(),'from_realm':'SR1','to_realm':'SR2','portal_id':'fixture.portal'}
        world.net.send(client,client.switch(intent,'BEGIN'))
        if replace_session:
            client=world.join('alice','SR1')
            assert client.welcome['pending_transition']==intent
        session=world.server.sessions[client.session_id]
        assert store.pending_transition('alice')==session.transition==intent
        changed=(dict(intent,from_realm='SR2',to_realm='SR1') if mutation=='reverse-realms'
                 else dict(intent,portal_id='different.portal'))
        world.net.send(client,client.switch(changed,phase))
        assert client.rejections,'a modified completion was accepted with the original transition id'
        assert client.rejections[-1]['code']=='WRONG_SESSION'
        assert session.realm=='SR1'
        assert session.status=='IN_TRANSITION'
        assert session.transition==store.pending_transition('alice')==intent
        assert next(p for p in world.server.snapshot()['player_list'] if p['player_id']=='alice')['realm']=='SR1'
        # A refused forged completion must not consume or poison the real completion.
        world.net.send(client,client.switch(dict(intent),phase))
        assert session.realm==('SR2' if phase=='COMMIT' else 'SR1')
        assert session.status=='ONLINE'
        assert session.transition is None
        assert store.pending_transition('alice') is None
    finally:
        store.close()
