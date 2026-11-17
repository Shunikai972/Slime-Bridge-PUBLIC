"""Inert event/thread boundaries measure actual launcher loop cadence without a game or real thread."""
from unittest import mock
from apps.launcher import desktop


def test_idle_polls_are_slow_but_active_handshake_stays_fast_and_no_duplicate_host_snapshot():
    api=object.__new__(desktop.LauncherAPI)
    api._travel_thread=None
    class Stop:
        def __init__(self):self.delays=[]
        def clear(self):pass
        def wait(self,delay):
            self.delays.append(delay)
            return len(self.delays)>4
    api._travel_stop=Stop()
    api._travel=mock.Mock()
    api._travel.poll.side_effect=[[],[{'state':'PREPARING'}],[{'state':'HOLDING'}],[{'state':'COMPLETE'}]]
    api._host=mock.Mock()
    api._host.state.side_effect=AssertionError('TravelCoordinator.poll already owns the host snapshot')
    def thread(*,target,**kwargs):
        result=mock.Mock()
        result.start.side_effect=target
        return result
    with mock.patch.object(desktop.threading,'Thread',side_effect=thread):
        api._ensure_travel_loop()
    assert api._travel_stop.delays==[.25,.25,.05,.05,.25]
    assert api._travel.poll.call_count==4
    api._host.state.assert_not_called()
