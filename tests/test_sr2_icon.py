"""Source Sprite metadata regression; proprietary source files are not fixtures."""
import tempfile
import unittest
from pathlib import Path


class SourceIconTests(unittest.TestCase):
    def test_full_source_texture_not_tight_crop_is_exported(self):
        from bridge.content.sr2_icon import build_icon_descriptor
        tree = {
            'm_Name': 'iconSlimeRingtail', 'm_Rect': dict(x=0, y=0, width=1024, height=1024),
            'm_Pivot': dict(x=.5, y=.5), 'm_PixelsToUnits': 100,
            'm_Border': dict(x=0, y=0, z=0, w=0),
            'm_RD': {'textureRect': dict(x=17.07612, y=43.07612, width=1006.9239, height=951.8478),
                     'textureRectOffset': dict(x=17.07612, y=43.07612), 'settingsRaw': 64,
                     'uvTransform': dict(x=100, y=512, z=100, w=512), 'downscaleMultiplier': 1,
                     'texture': dict(m_FileID=0, m_PathID=1324), 'alphaTexture': dict(m_FileID=0, m_PathID=0)},
        }
        descriptor = build_icon_descriptor(tree, (1024, 1024), [[-5.12,-5.12],[5.12,-5.12],[0,5.12]], [0,1,2])
        self.assertEqual([0,0,1024,1024], descriptor['rect'])
        self.assertEqual([.5,.5], descriptor['pivot'])
        self.assertEqual([17.07612,43.07612], descriptor['source_texture_rect_offset'])
        self.assertEqual([1024,1024], descriptor['texture_size'])
        self.assertEqual(64, descriptor['source_settings_raw'])
        self.assertTrue(descriptor['mandatory'])
        from copy import deepcopy
        for path, value in [('m_Pivot', dict(x=.1, y=.5)), ('m_PixelsToUnits', 50),
                            ('m_AtlasTags', ['packed']), ('m_RD.settingsRaw', 65),
                            ('m_RD.uvTransform', dict(x=100,y=0,z=100,w=0))]:
            candidate = deepcopy(tree)
            target = candidate
            keys = path.split('.')
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_icon_descriptor(candidate, (1024,1024), [[0,0],[1,0],[0,1]], [0,1,2])
        with self.assertRaises(ValueError):
            build_icon_descriptor(tree, (1024,1024), [[0,0],[1,0],[0,1]], [0,1,3])
        with self.assertRaises(ValueError):
            build_icon_descriptor(tree, (1024,1024), [[float('nan'),0],[1,0],[0,1]], [0,1,2])

    def test_generic_descriptor_keeps_species_source_identity(self):
        from bridge.content import sr2_icon
        tree = {
            'm_Name': 'iconSlimeCotton', 'm_Rect': dict(x=0, y=0, width=1024, height=1024),
            'm_Pivot': dict(x=.5, y=.5), 'm_PixelsToUnits': 100,
            'm_Border': dict(x=0, y=0, z=0, w=0),
            'm_RD': {'textureRect': dict(x=19.5, y=23.5, width=1000, height=950),
                     'textureRectOffset': dict(x=19.5, y=23.5), 'settingsRaw': 64,
                     'uvTransform': dict(x=100, y=512, z=100, w=512), 'downscaleMultiplier': 1,
                     'texture': dict(m_FileID=0, m_PathID=1359), 'alphaTexture': dict(m_FileID=0, m_PathID=0)},
        }
        result = sr2_icon.build_source_icon_descriptor(tree, (1024, 1024),
            [[-5.12, -5.12], [5.12, -5.12], [0, 5.12]], [0, 1, 2],
            sprite_id=3065, appearance_id=31291)
        self.assertEqual('iconSlimeCotton.source.png', result['file'])
        self.assertEqual('iconSlimeCotton', result['source_sprite_name'])
        self.assertEqual(3065, result['source_sprite_path_id'])
        self.assertEqual(31291, result['source_appearance_path_id'])
        self.assertEqual(1359, result['source_texture_path_id'])
        self.assertEqual([19.5, 23.5], result['source_texture_rect_offset'])
        with self.assertRaises(ValueError):
            sr2_icon.build_icon_descriptor(tree, (1024, 1024), [[0,0], [1,0], [0,1]], [0,1,2])

    def test_generic_export_uses_exact_texture_object_not_name_or_crop(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        from bridge.content import sr2_icon
        tree = {'m_Name': 'iconSlimeCotton', 'm_Rect': dict(x=0,y=0,width=1024,height=1024),
            'm_Pivot': dict(x=.5,y=.5), 'm_PixelsToUnits': 100, 'm_Border': dict(x=0,y=0,z=0,w=0),
            'm_RD': {'textureRect': dict(x=0,y=0,width=1024,height=1024),
                'textureRectOffset': dict(x=0,y=0), 'settingsRaw': 64,
                'uvTransform': dict(x=100,y=512,z=100,w=512), 'downscaleMultiplier': 1,
                'texture': dict(m_FileID=0,m_PathID=1359), 'alphaTexture': dict(m_FileID=0,m_PathID=0)}}
        mesh = SimpleNamespace(process=lambda: None, m_Vertices=[[-1,-1],[1,-1],[0,1]],
                               get_triangles=lambda: [[[0,1,2]]])
        module = SimpleNamespace(MeshHandler=lambda *args: mesh)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory/'resources.assets').write_bytes(b'synthetic-source')
            image = SimpleNamespace(size=(1024,1024), save=lambda path: Path(path).write_bytes(b'exact-source-image'))
            image.convert = lambda mode: image
            def obj(pid, kind, **kwargs):
                return SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind), **kwargs)
            texture = obj(1359, 'Texture2D', read=lambda: SimpleNamespace(image=image))
            sprite = obj(3065, 'Sprite', read_typetree=lambda: tree,
                         read=lambda: SimpleNamespace(m_RD=object()), version=(2021,3))
            appearance = obj(31291, 'MonoBehaviour', read_typetree=lambda: {'_icon': dict(m_FileID=0,m_PathID=3065)})
            assets = SimpleNamespace(data_dir=directory, _objects=[appearance,sprite,texture])
            output = directory/'output'; output.mkdir()
            with patch.dict(sys.modules, {'UnityPy.helpers.MeshHelper': module}):
                descriptor = sr2_icon.export_source_icon(assets,
                    {'appearance_id': 31291, 'sprite_id': 3065}, output)
            self.assertEqual(b'exact-source-image', (output / descriptor['file']).read_bytes())
            self.assertEqual(1359, descriptor['source_texture_path_id'])
            self.assertEqual(3065, descriptor['source_sprite_path_id'])

    def test_generic_descriptor_rejects_malformed_declared_source(self):
        from bridge.content import sr2_icon
        from copy import deepcopy
        tree = {'m_Name': 'iconSlimeExample', 'm_Rect': dict(x=0,y=0,width=1024,height=1024),
            'm_Pivot': dict(x=.5,y=.5), 'm_PixelsToUnits': 100, 'm_Border': dict(x=0,y=0,z=0,w=0),
            'm_RD': {'textureRect': dict(x=0,y=0,width=1024,height=1024),
                'textureRectOffset': dict(x=0,y=0), 'settingsRaw': 64,
                'uvTransform': dict(x=100,y=512,z=100,w=512), 'downscaleMultiplier': 1,
                'texture': dict(m_FileID=0,m_PathID=8), 'alphaTexture': dict(m_FileID=0,m_PathID=0)}}
        for path, value in [('m_RD.texture.m_PathID', True), ('m_RD.alphaTexture.m_FileID', 1),
                ('m_RD.settingsRaw', 64.0), ('m_Rect.x', False),
                ('m_RD.textureRect.x', float('nan')), ('m_RD.textureRect.width', 1025),
                ('m_RD.textureRectOffset.x', 10), ('m_Name', '../unsafe')]:
            candidate = deepcopy(tree); target = candidate; keys = path.split('.')
            for key in keys[:-1]: target = target[key]
            target[keys[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                sr2_icon.build_source_icon_descriptor(candidate, (1024,1024), [[0,0],[1,0],[0,1]], [0,1,2],
                    sprite_id=2, appearance_id=4)
        with self.assertRaises(ValueError):
            sr2_icon.build_source_icon_descriptor(tree, (1024,1024), [[0,0],[6,0],[0,1]], [0,1,2],
                sprite_id=2, appearance_id=4)

    def test_private_output_is_fresh_and_never_canonical(self):
        from bridge.content.sr2_icon import _fresh_private_output, REPO_ROOT
        with self.assertRaises(ValueError):
            _fresh_private_output(REPO_ROOT / 'Lab' / 'AssetCache' / 'sr2-to-sr1' / 'slime.ringtail')
        private = REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private, prefix='icon-output-test-') as directory:
            with self.assertRaises(ValueError):
                _fresh_private_output(Path(directory))


    def test_output_traversal_is_refused_before_creating_directory(self):
        from unittest.mock import patch
        from bridge.content.sr2_icon import _fresh_private_output, REPO_ROOT
        with patch.object(Path, 'mkdir', side_effect=AssertionError('outside private Lab write')):
            for path in [REPO_ROOT / 'Lab' / '..' / 'icon-escape-test',
                         REPO_ROOT / 'Lab' / 'Games' / 'icon-escape-test']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    _fresh_private_output(path)


if __name__ == '__main__':
    unittest.main()
