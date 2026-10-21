"""Synthetic demo world for the launcher (``--demo``): every page populated without any real game.

Everything is SYNTHETIC and labelled so: two detected Lab installs, profiles driven through the real
counterpart stack by the counterpart simulator (linked / pending / conflicted), the fixture content
universe converted in both directions, a deployment inventory, a hybrid server with players in both
realms, directory listings, portals, recovery issues and an (honestly blocked) update check. Nothing
here touches a game, a native save or the network.
"""

import hashlib
import json
import tempfile
from pathlib import Path

from bridge.app import installations as I
from bridge.app.content import catalogue
from bridge.app.recovery import issues
from bridge.app.saves import SaveManager, StaticCatalog
from bridge.app.settings import DEFAULTS, update
from bridge.app.updates import RejectAll, plan_updates
from bridge.conversion.batch import run_batch
from bridge.conversion.engine import SourceBundle
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.core import diagnostics as D
from bridge.counterpart.simulator import SCENARIOS as CP_SCENARIOS, CounterpartSimulation
from bridge.counterpart.world_ready import evaluate_world_ready
from bridge.deploy.executor import DeployExecutor, DeployJournal, MemoryFS
from bridge.deploy.planner import plan_deploy
from bridge.deploy.units import make_unit
from bridge.multiplayer.auth import DevTokenAuth
from bridge.multiplayer.directory import InMemoryDirectory, listing_from_server
from bridge.multiplayer.server import BridgeServer, ServerConfig
from bridge.multiplayer.transport import Client, Network
from bridge.portal.contracts import DEFAULT_PORTALS, evaluate
from bridge.transition.simulator import SCENARIOS as TR_SCENARIOS, SimRealmProcess, TransitionSimulation, provider_for

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / 'fixtures' / 'cloud' / 'pipeline'
DEMO_TOKENS = {f'demo-token-{name}-000000': name for name in ('ranch_hand', 'pip', 'mochi', 'juniper', 'you')}
CONTENT_SR1, CONTENT_SR2 = '1' * 64, '2' * 64


def _json(name):
    return json.loads((PIPELINE / name).read_text(encoding='utf-8'))


def fixture_batches():
    """Both conversion directions over the committed synthetic content universe."""
    out = {}
    for source, target in (('SR2', 'SR1'), ('SR1', 'SR2')):
        bundle = SourceBundle(ManifestSource(_json(f'source-{source.lower()}.json')), AttributeMaterialMapper(source),
                              _json(f'material-profile-{source.lower()}.json'),
                              _json(f'behaviour-profile-{source.lower()}.json'))
        out[f'{source}->{target}'] = run_batch(source=bundle,
                                               destination_registry=ManifestSource(_json(f'source-{target.lower()}.json')),
                                               target_profile=_json(f'target-profile-{target.lower()}.json'))
    return out


DEMO_BUILD = 'sim-build-1'   # the synthetic build every demo realm runs


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class DemoWorld:
    synthetic = True

    def __init__(self, root=None):
        self._tmp = None
        if root is None:
            self._tmp = tempfile.TemporaryDirectory(prefix='slimebridge-demo-')
            root = self._tmp.name
        self.root = Path(root)
        self._transition_simulations = []
        self.settings = update(DEFAULTS, 'advanced', {'synthetic_demo': True})
        self.installations = I.StaticInstallations([
            I.record('SR1', 'Lab/Games/SR1', fingerprint='a' * 64, build='synthetic-sr1-1.4.4',
                     loader_version='synthetic', plugin_version='0.9.0-demo'),
            I.record('SR2', 'Lab/Games/SR2', fingerprint='b' * 64, build='synthetic-sr2-0.6.2',
                     loader_version='synthetic', plugin_version='0.9.0-demo'),
            I.record('SR1', 'C:/Games/Slime Rancher', role='SOURCE', store='steam'),
        ])
        self._build_profiles()
        self.batches = fixture_batches()
        self._build_content()
        self._build_servers()
        self.portals = dict(DEFAULT_PORTALS)
        self.update_plan = plan_updates({'launcher': '0.9.0', 'bridgehost': '0.9.0'}, _demo_manifest(),
                                        verifier=RejectAll())

    # -- profiles ---------------------------------------------------------------------------------

    def _build_profiles(self):
        self.simulations = {}
        for name, scenario in (('linked', 'sr1_to_sr2'), ('pending', 'send_failed_before_delivery'),
                               ('conflicted', 'wrong_readback'), ('reverse', 'sr2_to_sr1')):
            path = self.root / 'profiles' / name
            path.mkdir(parents=True, exist_ok=True)
            sim = CounterpartSimulation(path, **CP_SCENARIOS[scenario][0])
            sim.run()
            self.simulations[name] = sim
        self.profile_labels = {self.simulations['linked'].profile_id: 'Sunny Meadow Ranch',
                               self.simulations['pending'].profile_id: 'Coral Cove',
                               self.simulations['conflicted'].profile_id: 'Old Quarry Run',
                               self.simulations['reverse'].profile_id: 'Starlight Strand'}

        def environment(realm):
            return {'health': 'HEALTHY', 'slot_probe': {'complete': True, 'free_slots': [1, 2]}, 'runtime_proven': True,
                    'build': DEMO_BUILD}
        self.save_managers = [SaveManager(sim.service, StaticCatalog([sim.profile_id]), environment)
                              for sim in self.simulations.values()]
        for sim in self.simulations.values():
            snap = sim.store.get(sim.profile_id)
            for realm in ('SR1', 'SR2'):
                if snap[realm.lower()] is not None and realm == sim.source:
                    sim.service.observe_live(sim.profile_id, realm, build_fingerprint=DEMO_BUILD)

    def set_game_path(self, realm, path):
        games = dict(self.settings['games'])
        games[realm] = dict(games[realm], path=path)
        self.settings = update(self.settings, 'games', games)  # demo: remembered in memory only

    def live_session(self, realm):
        return {'pid': 4100 if realm == 'SR1' else 4200, 'session_id': f'demo-{realm.lower()}'}

    def pairs(self):
        out = []
        for manager in self.save_managers:
            for pair in manager.pairs():
                out.append({**pair, 'label': self.profile_labels.get(pair['profile_id'], pair['profile_id'][:8])})
        return out

    def _manager(self, profile_id):
        for manager in self.save_managers:
            if profile_id in manager.catalog.profile_ids():
                return manager
        raise KeyError(profile_id)

    def save_detail(self, profile_id):
        return self._manager(profile_id).detail(profile_id)

    def decision(self, profile_id, target_realm):
        """The counterpart policy's own decision (linked pairs included), never derived from endpoint presence."""
        return self._manager(profile_id).decision(profile_id, target_realm)

    def world_ready(self, realm, endpoint, session):
        """Synthetic WORLD_READY: a simulated process reports the endpoint's world; the verdict still comes from
        ``evaluate_world_ready`` (binding + trust), with the sim provider trusted only inside the demo."""
        process = SimRealmProcess(realm, endpoint, pid=session['pid'])
        process.session = dict(session)
        process.loaded = {k: endpoint[k] for k in ('native_save_id', 'native_save_identity')}
        expected = {**{k: endpoint[k] for k in ('realm', 'installation_id', 'native_save_id', 'native_save_identity')},
                    'build_fingerprint': DEMO_BUILD, 'session': dict(session)}
        return evaluate_world_ready(process.world_ready_report(), expected=expected,
                                    trust={provider_for(realm)['provider_id']: realm})

    # -- content ----------------------------------------------------------------------------------

    def _build_content(self):
        self.deploy_journal = DeployJournal(self.root / 'deploy.sqlite3')
        self.deploy_fs = MemoryFS()
        blobs = {}

        def unit(cid, realm, status='EXPERIMENTAL'):
            files = []
            for part in ('mesh.bin', 'body.tex'):
                data = f'synthetic {cid} {part}'.encode()
                digest = hashlib.sha256(data).hexdigest()
                blobs[digest] = data
                files.append({'path': f'generated/{cid}/{part}', 'sha256': digest})
            return make_unit(key=f'generated:{cid}:{realm}', kind='GENERATED', realm=realm, version=[1, 0, 0],
                             files=files, provides=[cid], status=status, source_ref='synthetic-demo')
        executor = self.deploy_executor = DeployExecutor(self.deploy_journal, self.deploy_fs, blobs.get)
        for realm, desired in (('SR1', [('slime.azure', 'ENABLED'), ('slime.prism', 'ENABLED'), ('slime.moss', 'DISABLED')]),
                               ('SR2', [('slime.quartz', 'ENABLED')])):
            plan = plan_deploy(self.deploy_journal.inventory(realm),
                               [{'key': f'generated:{cid}:{realm}', 'state': state, 'unit': unit(cid, realm)}
                                for cid, state in desired], history=self.deploy_journal.history())
            executor.apply(plan)
        self.refresh_content()

    def refresh_content(self):
        self.content = catalogue(self.batches, inventories={r: self.deploy_journal.inventory(r) for r in ('SR1', 'SR2')})

    def diagnostics_dir(self):
        path = self.root / 'diagnostics'
        path.mkdir(exist_ok=True)
        return path

    def import_largos(self, folder):
        from bridge.app.largos import library
        profiles = [_json(f'target-profile-{r.lower()}.json') for r in ('SR1', 'SR2')]
        parents = {row['canonical_id']: ['standard.vacpack'] for row in self.content['rows']}
        context = {r: {'native': set(), 'parents': set(parents)} for r in ('SR1', 'SR2')}
        self._largos = library(folder, ledger=getattr(self, '_largo_ledger', {}), known_base_species=set(parents),
                               target_profiles=profiles, parent_capabilities=parents, target_context=context)
        self._largo_ledger = self._largos['ledger']
        return self._largos['rows']

    def largo_library(self):
        return getattr(self, '_largos', {'rows': []})

    def mods(self):
        """The synthetic mod corpus (fictional fixture mods), analysed statically."""
        if not hasattr(self, '_mods'):
            from bridge.app.mods import rows
            from bridge.modcompat import manifest as M
            entries = []
            for path in sorted((ROOT / 'fixtures' / 'cloud' / 'mods').glob('case-*.json')):
                case = json.loads(path.read_text(encoding='utf-8'))
                data = case['input']
                raw = data['raw_text'] if data['raw_text'] is not None else data['document']
                manifest, diags = M.parse_manifest(data['format'], raw, source=data['source'], files=data['files'])
                entries.append({'manifest': manifest, 'diagnostics': diags, 'evidence': case.get('evidence'),
                                'label': case['case']})
            self._mods = rows(entries)
        return self._mods

    # -- multiplayer ------------------------------------------------------------------------------

    def _build_servers(self):
        self.clock = _Clock()
        self.server = BridgeServer(ServerConfig(name='Sunny Meadow Co-op', mode='HYBRID', max_players=8,
                                                required_content={'SR1': CONTENT_SR1, 'SR2': CONTENT_SR2},
                                                motd='Welcome to the Bridge! Both ranches are open.'),
                                   DevTokenAuth(DEMO_TOKENS, development_only=True), clock=self.clock)
        self.network = Network(self.server)
        for name, realm in (('ranch_hand', 'SR1'), ('pip', 'SR2'), ('mochi', 'SR2'), ('juniper', None)):
            client = self.network.join(Client(name.replace('_', ' ').title(), f'demo-token-{name}-000000',
                                              content={'SR1': CONTENT_SR1, 'SR2': CONTENT_SR2}, realm=realm))
            self.network.send(client, client.hello())
        talker = next(iter(self.network.clients.values()))
        self.network.send(talker, talker.chat('Anyone heading to the sanctuary gate?'))
        local = listing_from_server(self.server, server_id='local', address='127.0.0.1:7777', ping_ms=1,
                                    description='Hosted on this PC (demo)')

        def listing(server_id, name, mode, players, populations, *, versions=(1,), content=None, ping=None, desc=''):
            return {**local, 'server_id': server_id, 'name': name, 'mode': mode, 'players': players,
                    'max_players': 16, 'realm_populations': populations, 'protocol_versions': list(versions),
                    'content': content if content is not None else {}, 'ping_ms': ping, 'description': desc,
                    'source': 'DIRECTORY', 'address': f'{server_id}.example:7777'}
        self.directory = InMemoryDirectory([
            local,
            listing('meadow-hub', 'Meadow Hub', 'HYBRID', 9, {'SR1': 5, 'SR2': 4}, ping=38,
                    content={'SR1': {'manifest_hash': CONTENT_SR1, 'required_packages': []},
                             'SR2': {'manifest_hash': '9' * 64, 'required_packages': ['generated:slime.azure:SR2']}},
                    desc='Friendly hybrid ranch, needs the Azure pack in SR2'),
            listing('classic-dunes', 'Classic Dunes', 'SR1_ONLY', 3, {'SR1': 3}, ping=61, desc='SR1 veterans only'),
            listing('future-labs', 'Future Labs', 'SR2_ONLY', 2, {'SR2': 2}, versions=(2,), ping=120,
                    desc='Runs a newer Bridge protocol'),
        ])

    # -- transitions / portals / recovery ----------------------------------------------------------

    def transition_simulation(self, scenario='sr1_to_sr2'):
        path = Path(tempfile.mkdtemp(dir=self.root, prefix='transition-'))
        simulation = TransitionSimulation(path, **TR_SCENARIOS[scenario][0])
        self._transition_simulations.append(simulation)
        return simulation

    def portal_states(self):
        linked = self.simulations['linked']
        decision = linked.service.decide(linked.profile_id, 'SR2', provider_health='HEALTHY', runtime_proven=True,
                                         slot_probe={'complete': True, 'free_slots': [1]}, current_build=DEMO_BUILD)
        base = {'unlocked': False, 'balance': 12, 'flags': set(), 'known_species': set(),
                'counterpart': decision['decision'], 'transition_stage': None, 'provider_available': True,
                'cooldown_remaining': 0}
        return {pid: evaluate(portal, dict(base, balance=12 if portal['source_realm'] == 'SR1' else 180000))
                for pid, portal in self.portals.items()}

    def diagnostics(self):
        diags = []
        for realm, view in I.overview(self.installations).items():
            diags += view['diagnostics']
        for sim in self.simulations.values():
            for intent in sim.journal.intents_for(sim.profile_id):
                if intent['phase'] == 'UNCERTAIN':
                    diags.append(D.make('COUNTERPART_RECOVERED_UNCERTAIN', 'warning', 'counterpart',
                                        'a partner save creation could not be confirmed yet',
                                        subject=intent['creation_id'], profile_id=sim.profile_id,
                                        realm=intent['target_realm'], action='RECONCILE'))
                if intent['phase'] == 'CONFLICT':
                    diags.append(D.make('COUNTERPART_LINK_REFUSED', 'error', 'counterpart',
                                        'verification of the partner save failed; it was not linked',
                                        subject=intent['creation_id'], profile_id=sim.profile_id,
                                        realm=intent['target_realm']))
        for entry in self.batches['SR2->SR1']['report']['entries']:
            if entry['final'] == 'QUARANTINED':
                diags.append(D.make('PACKAGE_QUARANTINED', 'warning', 'package', f"{entry['canonical_id']} was quarantined",
                                    content_id=entry['canonical_id'], details={'reasons': entry['reasons'][:3]}))
        diags += self.update_plan['blockers']
        return issues(diags)

    def close(self):
        for sim in self._transition_simulations:
            sim.close()
        self._transition_simulations.clear()
        for sim in self.simulations.values():
            sim.close()
        self.deploy_journal.close()
        if self._tmp is not None:
            self._tmp.cleanup()


def _demo_manifest():
    return {'schema': 'bridge-update-manifest', 'schema_version': 1, 'channel': 'stable', 'sequence': 2,
            'components': {'launcher': {'version': '1.0.0', 'min_from': None, 'restart': 'LAUNCHER', 'requires': {},
                                        'migrations': [], 'artifacts': [{'name': 'launcher.bin', 'sha256': 'c' * 64,
                                                                         'size': 1024}]}},
            'signature': {'scheme': 'none', 'key_id': 'demo', 'value': 'unsigned-demo'}}
