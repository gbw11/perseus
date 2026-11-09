"""PC 코스 선택/새 실행 준비만 검사한다. simulator·제품 import나 실행은 없다."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from course_catalog import (load_catalog,plan_bytes,SCENE,PROFILE,PROFILE_TEST,TRACKS,LEGACY_MAIN,NATIVE,LEGACY,PLAN_SCHEMA)
from snapshot import SnapshotError

REPOSITORY=Path(__file__).resolve().parents[5]
SOURCES=(SCENE,PROFILE,PROFILE_TEST,TRACKS,LEGACY_MAIN)


class CourseCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for name in SOURCES:
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((REPOSITORY/name).read_bytes())
        self.catalog=load_catalog(self.root)

    def hashes(self):
        return {name:sha256((self.root/name).read_bytes()).hexdigest() for name in SOURCES}

    def test_actual_native_fixture_first_and_only_actual_legacy_track_ids_are_listed(self):
        self.assertEqual(self.catalog.unavailable,());self.assertEqual(len(self.catalog.entries),11)
        native=self.catalog.entries[0];self.assertEqual((native.key,native.family),('native:jetson-demo',NATIVE))
        expected=json.loads((self.root/TRACKS).read_bytes())['tracks']
        self.assertEqual([(c.track_id,c.name) for c in self.catalog.entries[1:]],[(x['id'],x['name']) for x in expected])
        self.assertTrue(all(c.family==LEGACY for c in self.catalog.entries[1:]))
        self.assertNotIn('native:jetson-r75',{c.key for c in self.catalog.entries})
        self.assertNotIn('P01-straight-empty',{c.track_id for c in self.catalog.entries})

    def test_native_plan_is_existing_fixture_check_and_never_claims_start_or_apply(self):
        before=self.hashes()
        with patch('subprocess.run',side_effect=AssertionError('must never execute')):
            plan=self.catalog.prepare('native:jetson-demo')
        self.assertEqual(plan['schema'],PLAN_SCHEMA);self.assertEqual(plan['state'],'PREPARED_NOT_EXECUTED')
        self.assertEqual(plan['definition'],dict(scene_path=SCENE,profile_path=PROFILE))
        self.assertEqual(plan['manual_recipe']['purpose'],'FIXTURE_PROFILE_CHECK_ONLY_NOT_DRIVING')
        self.assertEqual(plan['manual_recipe']['cwd'],'vehicle/Jetson')
        self.assertEqual(plan['manual_recipe']['argv'],['python','-m','unittest','discover','-s','tests/unit','-p','test_simulation_profile.py','-v'])
        self.assertEqual(plan['connection']['simulator_start_ipc'],'NOT_CONNECTED')
        self.assertFalse(plan['connection']['current_run_applied']);self.assertFalse(plan['connection']['jetson_map_modified'])
        self.assertFalse(plan['connection']['nn_or_source_restarted']);self.assertEqual(before,self.hashes())

    def test_two_laps_keep_one_run_map_and_no_unsupported_lap_cli_or_reset(self):
        first=self.catalog.prepare('native:jetson-demo',requested_laps=2);again=self.catalog.prepare('native:jetson-demo',requested_laps=2)
        self.assertEqual(plan_bytes(first),plan_bytes(again))
        self.assertEqual(first['request']['lap_policy'],'SAME_RUN_AND_MAP_ACROSS_LAPS')
        self.assertEqual(first['request']['automatic_lap_count_execution'],'NOT_CONNECTED')
        for c in self.catalog.entries:
            value=self.catalog.prepare(c.key,requested_laps=2);argv=value['manual_recipe']['argv']
            self.assertEqual(value['request']['scope'],'NEW_RUN_ONLY');self.assertNotIn('--laps',argv)
            for excluded in ('reset','race.start','track.select','map_id','run_id','control_generation'):
                self.assertNotIn(excluded,argv);self.assertNotIn(excluded,value)

    def test_legacy_recipe_uses_actual_existing_cli_and_declares_protocol_gap(self):
        tree=ast.parse((self.root/LEGACY_MAIN).read_text(encoding='utf-8'))
        flags={a.value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_argument'
               for a in n.args if isinstance(a,ast.Constant) and isinstance(a.value,str)}
        plan=self.catalog.prepare('legacy:monza');recipe=plan['manual_recipe'];argv=recipe['argv']
        self.assertEqual(argv[argv.index('--track')+1],'monza')
        self.assertTrue({a for a in argv if a.startswith('--')}<=flags)
        self.assertEqual(recipe['purpose'],'LEGACY_NEW_SIMULATOR_PROCESS_MANUAL_ONLY')
        self.assertIn('LEGACY_MJPEG_320x240_NOT_NATIVE_DEPTH',plan['limitations'])
        self.assertIn('LEGACY_WRITE_ONLY_FRAMES_NOT_FEEDBACK3',plan['limitations'])
        self.assertIn('CURRENT_JETSON_HIL_INTEGRATION_NOT_CONNECTED',plan['limitations'])

    def test_change_selection_only_changes_dev_plan_not_recording_or_saved_map(self):
        before=self.hashes();one=self.catalog.prepare('native:jetson-demo');two=self.catalog.prepare('legacy:monza')
        self.assertNotEqual(one['selected_course'],two['selected_course']);self.assertEqual(before,self.hashes())
        self.assertEqual(one['excluded'],['HISTORICAL_SCENE_RECORDING_SELECTION','JETSON_SAVED_MAP_SELECTION','RUNNING_COURSE_HOT_SWAP'])
        self.assertNotIn('map',one);self.assertNotIn('media',one);self.assertNotIn('source',one)
        self.assertFalse(any(n.startswith(('xenova_vehicle','xenova_gateway','xenova_simulator')) for n in sys.modules))

    def test_manifest_download_is_display_only_plan_and_does_not_embed_gt_geometry(self):
        plan=self.catalog.prepare('native:jetson-demo');raw=plan_bytes(plan);decoded=json.loads(raw)
        self.assertEqual(decoded,plan);self.assertLess(len(raw),16384)
        for word in ('centerline','obstacle_seeds','parking_slots','pose'):
            self.assertNotIn(word,decoded);self.assertNotIn('"'+word+'"',raw.decode())
        for entry in plan['source_artifacts']:
            payload=(self.root/entry['path']).read_bytes();self.assertEqual(entry['bytes'],len(payload));self.assertEqual(entry['sha256'],sha256(payload).hexdigest())

    def test_unknown_injected_course_and_invalid_lap_requests_are_rejected(self):
        for key in ('native:missing','native:jetson-demo; echo bad','../outside','P01-straight-empty',None,False):
            with self.subTest(key=key),self.assertRaisesRegex(SnapshotError,'COURSE_NOT_IN_SUPPORTED_CATALOG'):self.catalog.prepare(key)
        for laps in (True,False,0,17,2.,'2',None):
            with self.subTest(laps=laps),self.assertRaisesRegex(SnapshotError,'COURSE_REQUESTED_LAPS'):self.catalog.prepare('native:jetson-demo',requested_laps=laps)

    def test_changed_definition_after_selection_requires_new_catalog_not_old_apply(self):
        path=self.root/SCENE;path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(SnapshotError,'COURSE_DEFINITION_CHANGED'):self.catalog.prepare('native:jetson-demo')
        updated=load_catalog(self.root);self.assertFalse(any(x.family==NATIVE for x in updated.entries))
        self.assertTrue(any('SOURCE_MISMATCH' in x for x in updated.unavailable))
        self.assertEqual(len(updated.entries),10)

    def test_missing_or_invalid_native_profile_preserves_other_family_and_reason(self):
        path=self.root/PROFILE;original=path.read_bytes()
        for raw in (b'{',original.replace(b'"physical_admission": false',b'"physical_admission": true')):
            path.write_bytes(raw);catalog=load_catalog(self.root)
            self.assertEqual(len(catalog.entries),10);self.assertTrue(catalog.unavailable)
        path.unlink();catalog=load_catalog(self.root);self.assertEqual(len(catalog.entries),10)
        self.assertIn(NATIVE,catalog.unavailable[0])

    def test_bad_legacy_lists_are_not_invented_or_substituted_with_historical_cases(self):
        path=self.root/TRACKS;original=json.loads(path.read_bytes())
        variants=(dict(tracks=[]),dict(tracks=original['tracks']*4),dict(tracks=[original['tracks'][0]]*2),
            dict(tracks=[{**original['tracks'][0],'id':'monza;bad'}]))
        for value in variants:
            with self.subTest(value_count=len(value['tracks'])):
                path.write_text(json.dumps(value),encoding='utf-8');catalog=load_catalog(self.root)
                self.assertEqual(len(catalog.entries),1);self.assertEqual(catalog.entries[0].family,NATIVE);self.assertTrue(catalog.unavailable)

    def test_no_supported_files_means_unavailable_not_a_fake_default_course(self):
        with tempfile.TemporaryDirectory() as empty:
            catalog=load_catalog(empty)
        self.assertEqual(catalog.entries,());self.assertEqual(len(catalog.unavailable),2)


if __name__=='__main__':unittest.main()
