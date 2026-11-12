"""Readiness/API compatibility checks without starting an agent or issuing game actions."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from saruman.doctor import wait_for_filter
from saruman.mod_adapter import normalize_snapshot, probe_native_mods


class PreflightTests(unittest.TestCase):
    def test_filter_waits_for_models_with_a_finite_operator_grace(self):
        now = [0]
        def sleep(seconds): now[0] += seconds
        with patch("saruman.doctor.filter_is_ready", side_effect=[False, False, True]) as ready:
            self.assertTrue(wait_for_filter("http://filter", 20, clock=lambda: now[0], sleep=sleep))
        self.assertEqual(now[0], 4)
        self.assertEqual(ready.call_count, 3)
        with patch("saruman.doctor.filter_is_ready", return_value=False):
            self.assertFalse(wait_for_filter("http://filter", 3, clock=lambda: now[0], sleep=sleep))
        self.assertEqual(now[0], 7)

    def test_zero_grace_never_sleeps_or_bypasses_readiness(self):
        with patch("saruman.doctor.filter_is_ready", return_value=False), patch("time.sleep") as sleep:
            self.assertFalse(wait_for_filter("http://filter", 0, sleep=sleep))
        sleep.assert_not_called()

    def test_snapshot_preserves_native_facts_and_normalizes_supply_ownership(self):
        state = {"ok": True, "operation": "state", "enabled": False, "needs": [{"item":"minecraft:ender_pearl","missing":8}], "action":"IDLE"}
        snapshot = normalize_snapshot({"status":"ready", "defense_api":"addon-0.1", "defense_json":json.dumps(state),
                                       "defense_busy":False,"native_busy":True})
        self.assertEqual(snapshot["defense"]["action"], "IDLE")
        self.assertEqual(snapshot["defense"]["missingItems"], state["needs"])
        self.assertEqual(snapshot["defense"]["owner"], "unionclef")
        self.assertTrue(snapshot["native_supply"])
        old = normalize_snapshot({"status":"ready","defense_json":'{"owner":"IDLE","missingItems":[]}'})
        self.assertNotIn("native_supply", old)
        with self.assertRaises(RuntimeError):
            normalize_snapshot({"status":"ready","defense_api":"addon-0.1","defense_json":'{"ok":false}'})

    def test_probe_checks_exact_artifacts_and_the_correct_read_only_api(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as directory:
                api, tree, loader = Mock(), Mock(), Mock()
                defense_id = "auto_defense" if modern else "auto-defense"
                found, hashes = {}, {}
                for mod_id in ("altoclef", defense_id, "treegrow"):
                    path=Path(directory)/(mod_id+".jar")
                    body=mod_id.encode()
                    path.write_bytes(body)
                    hashes[mod_id]={hashlib.sha256(body).hexdigest()}
                    optional=Mock()
                    optional.isPresent.return_value=True
                    paths=optional.get.return_value.getOrigin.return_value.getPaths.return_value
                    paths.size.return_value=1
                    paths.iterator.return_value.next.return_value.toString.return_value=str(path)
                    found[mod_id]=optional
                absent=Mock();absent.isPresent.return_value=False
                loader.getModContainer.side_effect=lambda mod_id:found.get(mod_id,absent)
                api.get.return_value.state.return_value='{"ok":true,"enabled":false}'
                api.getStatusJson.return_value='{"enabled":false}'
                gateway=N(jvm=N(net=N(fabricmc=N(loader=N(api=N(FabricLoader=N(getInstance=lambda:loader))))),
                                dev=N(autodefense=N(api=N(AutoDefenseApi=api))),lab=N(treegrow=N(TreeGrowClient=tree))))
                with patch("saruman.mod_adapter.ARTIFACTS", hashes):
                    self.assertEqual(set(probe_native_mods(gateway)),set(hashes))
                    self.assertEqual(api.get.return_value.state.call_count,1 if modern else 0)
                    self.assertEqual(api.getStatusJson.call_count,0 if modern else 1)
                    tree.current.assert_called_once()
                    api.get.return_value.enable.assert_not_called()
                    api.get.return_value.disable.assert_not_called()
                    (Path(directory)/(defense_id+".jar")).write_bytes(b"different-build")
                    with self.assertRaisesRegex(RuntimeError,"Непроверенная сборка"):
                        probe_native_mods(gateway)
