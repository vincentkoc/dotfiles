#!/usr/bin/env python3
"""Isolated filesystem/vendor fixtures; no host apps, services, or downloads."""

import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("local_ai_setup", str(ROOT / "bin/local-ai-setup"))
spec = importlib.util.spec_from_loader(loader.name, loader)
setup = importlib.util.module_from_spec(spec)
loader.exec_module(setup)


def tree(path):
    return {str(p.relative_to(path)): ("link", os.readlink(p)) if p.is_symlink()
            else ("file", p.read_bytes(), p.stat().st_mtime_ns) if p.is_file()
            else ("dir", p.stat().st_mode) for p in path.rglob("*")}


class LocalAISetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local ai fixture ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home with spaces"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.profile = copy.deepcopy(setup.load_profile())
        self.blob = b"GGUF tiny fixture model, never inference weights\n"
        self.profile["blob_bytes"] = len(self.blob)
        self.profile["blob_sha256"] = hashlib.sha256(self.blob).hexdigest()
        self.manifest = json.dumps({"schemaVersion": 2, "config": {"size": 1, "digest": "sha256:" + "0" * 64},
                       "layers": [{"mediaType": "application/vnd.ollama.image.model",
                       "digest": "sha256:" + self.profile["blob_sha256"],
                       "size": len(self.blob)}]}, separators=(",", ":")).encode()
        self.profile["manifest_sha256"] = hashlib.sha256(self.manifest).hexdigest()
        self.calls = []

    def initialized_home(self, *, custom=False):
        native_home = self.home / ".lmstudio"
        native_home.mkdir()
        (self.home / ".lmstudio-home-pointer").write_text(str(native_home) + "\n")
        if custom:
            models = self.home / "custom models"
            models.mkdir()
            (native_home / "settings.json").write_text(json.dumps({"downloadsFolder": str(models), "other": True}))
        self.paths = setup.paths_for(self.home, self.profile, None, self.env)
        self.bin = self.home / "fake vendor binaries"
        self.bin.mkdir()
        for name in ("ollama", "lms"):
            path = self.bin / name
            path.write_text("#!/bin/sh\nexit 99\n")
            path.chmod(0o755)
        return self.paths

    def populate_cache(self):
        for key, data in (("manifest", self.manifest), ("blob", self.blob)):
            path = self.paths[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def native(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "/usr/sbin/lsof":
            return b"p123\nn127.0.0.1:11434\n"
        if argv[0] == str(self.bin / "ollama"):
            self.assertEqual(argv[1:], ["pull", self.profile["model"]])
            self.populate_cache()
        elif argv[0] == str(self.bin / "lms"):
            self.assertEqual(argv[1:4], ["import", str(self.paths["friendly"]), "--user-repo"])
            self.assertIn("--symbolic-link", argv)
            self.assertIn("--yes", argv)
            self.paths["lm_models"].mkdir(parents=True, exist_ok=True)
            if "--dry-run" not in argv:
                self.paths["lm_link"].parent.mkdir(parents=True)
                self.paths["lm_link"].symlink_to(self.paths["friendly"])
        else:
            self.fail("unexpected native command")
        return b""

    def http(self, url, **kwargs):
        if url == "http://127.0.0.1:11434/api/version":
            self.assertTrue(kwargs.get("local"))
            return b'{"version":"fixture"}'
        self.assertEqual(url, "https://registry.ollama.ai/v2/library/qwen3/manifests/4b-instruct-2507-q4_K_M")
        return self.manifest

    def apply(self, **kwargs):
        with patch.object(setup.sys, "platform", "darwin"), \
                patch.object(setup, "OLLAMA", self.bin / "ollama"), \
                patch.object(setup, "LMS", self.bin / "lms"), \
                patch.object(setup, "run", side_effect=self.native), \
                patch.object(setup, "get_bytes", side_effect=self.http):
            return setup.reconcile(self.home, self.profile, apply=True, env=self.env, **kwargs)

    def test_default_check_never_initializes_apps_network_or_home(self):
        before = tree(self.home)
        with patch.object(setup, "run", side_effect=AssertionError("check invoked app")), \
                patch.object(setup, "get_bytes", side_effect=AssertionError("check used network")), \
                patch.object(setup, "load_profile", return_value=self.profile), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.main(["--json"]), 1)
            self.assertFalse(json.loads(output.getvalue())["ready"])
        self.assertEqual(tree(self.home), before)
        self.assertFalse((self.home / ".lmstudio-home-pointer").exists())

    def test_one_pull_shared_links_and_idempotent_apply(self):
        self.initialized_home()
        self.paths["ini"].parent.mkdir(parents=True)
        original = b"; preserve this\n[existing]\nmodel=/example/other.gguf\n"
        self.paths["ini"].write_bytes(original)
        result = self.apply()
        self.assertTrue(result["ready"])
        self.assertEqual(sum("pull" in call for call in self.calls), 1)
        self.assertEqual(sum("import" in call for call in self.calls), 2)
        self.assertTrue(self.paths["friendly"].is_symlink())
        self.assertTrue(self.paths["lm_link"].is_symlink())
        self.assertEqual(self.paths["lm_link"].resolve(), self.paths["blob"].resolve())
        self.assertTrue(self.paths["ini"].read_bytes().startswith(original))
        before = tree(self.home)
        self.calls.clear()
        result = self.apply()
        self.assertEqual(result["changed"], [])
        self.assertEqual(tree(self.home), before)
        self.assertFalse(any("pull" in call or "import" in call for call in self.calls))
        with patch.object(setup, "run", side_effect=AssertionError("check invoked app")), \
                patch.object(setup, "get_bytes", side_effect=AssertionError("check used network")):
            self.assertTrue(setup.reconcile(self.home, self.profile)["ready"])
        self.assertEqual(tree(self.home), before)

    def test_remote_manifest_mismatch_before_pull_or_links(self):
        self.initialized_home()
        self.manifest += b" "
        with self.assertRaisesRegex(setup.SetupError, "manifest digest"):
            self.apply()
        self.assertFalse(any("pull" in call or "import" in call for call in self.calls))
        self.assertFalse(os.path.lexists(self.paths["friendly"]))

    def test_bad_model_size_or_hash_never_links_or_imports(self):
        self.initialized_home()
        for payload in (self.blob[:-1], self.blob + b"x", b"x" * len(self.blob)):
            with self.subTest(size=len(payload)):
                self.populate_cache()
                self.paths["blob"].write_bytes(payload)
                with self.assertRaises(setup.SetupError):
                    self.apply()
                self.assertEqual(self.calls, [])
                self.assertFalse(os.path.lexists(self.paths["friendly"]))

    def native_anchor(self):
        self.populate_cache()
        original = self.paths["store"] / "blobs" / ("sha256-" + self.profile["manifest_sha256"])
        original.write_bytes(self.manifest)
        document = json.loads(self.manifest)
        document["layers"].append({"mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                                   "digest": "sha256:" + self.profile["manifest_sha256"],
                                   "size": len(self.manifest)})
        self.paths["manifest"].write_text(json.dumps(document, indent=2, sort_keys=True))
        return original, document

    def test_exact_native_provenance_reuses_verified_cache_without_pull(self):
        self.initialized_home()
        self.native_anchor()
        self.assertTrue(self.apply()["ready"])
        self.assertFalse(any("pull" in call for call in self.calls))
        with patch.object(setup, "run", side_effect=AssertionError("check invoked app")), \
                patch.object(setup, "get_bytes", side_effect=AssertionError("check used network")):
            self.assertTrue(setup.reconcile(self.home, self.profile)["ready"])

    def test_native_provenance_rejects_changed_origin_or_descriptor(self):
        self.initialized_home()
        original, document = self.native_anchor()
        for field, value in (("size", 0), ("digest", "sha256:" + "1" * 64),
                             ("mediaType", "application/vnd.ollama.image.model")):
            bad = copy.deepcopy(document)
            bad["layers"][-1][field] = value
            self.paths["manifest"].write_text(json.dumps(bad))
            with self.subTest(field=field), self.assertRaises(setup.SetupError):
                self.apply()
        self.paths["manifest"].write_text(json.dumps(document))
        original.write_bytes(self.manifest + b" ")
        with self.assertRaisesRegex(setup.SetupError, "manifest digest mismatch"):
            self.apply()
        original.unlink()
        with self.assertRaisesRegex(setup.SetupError, "required local path is missing"):
            self.apply()
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.lexists(self.paths["friendly"]))

    def test_native_provenance_preserves_full_manifest_and_scalar_types(self):
        self.initialized_home()
        _, document = self.native_anchor()
        cases = []
        bad = copy.deepcopy(document); bad["config"]["size"] = True; cases.append(bad)
        bad = copy.deepcopy(document); bad["config"]["digest"] = "sha256:" + "2" * 64; cases.append(bad)
        bad = copy.deepcopy(document); bad["schemaVersion"] = 2.0; cases.append(bad)
        bad = copy.deepcopy(document); bad["layers"][0]["size"] += 1; cases.append(bad)
        bad = copy.deepcopy(document); bad["layers"].reverse(); cases.append(bad)
        bad = copy.deepcopy(document); bad["layers"].append(bad["layers"][-1].copy()); cases.append(bad)
        bad = copy.deepcopy(document); bad["extra"] = "unrecognized"; cases.append(bad)
        for bad in cases:
            self.paths["manifest"].write_text(json.dumps(bad))
            with self.subTest(document=bad), self.assertRaises(setup.SetupError):
                self.apply()
        duplicate = json.dumps(document)[:-1] + ', "schemaVersion": 2}'
        self.paths["manifest"].write_text(duplicate)
        with self.assertRaisesRegex(setup.SetupError, "duplicate manifest JSON key"):
            self.apply()
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.lexists(self.paths["friendly"]))

    def test_file_foreign_and_dangling_link_collisions_are_preserved(self):
        self.initialized_home()
        self.populate_cache()
        for name in ("friendly", "lm_link"):
            target = self.paths[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            foreign = self.home / "foreign.gguf"
            foreign.write_bytes(b"preserve")
            for kind in ("file", "foreign", "dangling"):
                with self.subTest(name=name, kind=kind):
                    if kind == "file": target.write_bytes(b"preserve")
                    else: target.symlink_to(foreign if kind == "foreign" else self.home / "missing")
                    before = tree(self.home)
                    with self.assertRaises((setup.SetupError, OSError)):
                        self.apply()
                    self.assertEqual(tree(self.home), before)
                    self.assertEqual(self.calls, [])
                    target.unlink()

    def test_custom_native_lmstudio_root_is_used_without_settings_change(self):
        self.initialized_home(custom=True)
        settings = self.home / ".lmstudio/settings.json"
        before = settings.read_bytes()
        self.assertTrue(self.apply()["ready"])
        self.assertTrue(self.paths["lm_link"].is_relative_to(self.home / "custom models"))
        self.assertEqual(settings.read_bytes(), before)
        self.assertFalse((self.home / ".lmstudio/models").exists())

    def test_custom_ollama_store_requires_explicit_selection(self):
        self.initialized_home()
        store = self.home / "custom ollama"
        store.mkdir()
        self.env["OLLAMA_MODELS"] = str(store)
        with self.assertRaisesRegex(setup.SetupError, "matching --ollama-models"):
            self.apply()
        self.assertEqual(self.calls, [])
        self.paths = setup.paths_for(self.home, self.profile, store, self.env)
        self.assertTrue(self.apply(explicit_store=store)["ready"])
        self.assertFalse((self.home / ".ollama").exists())

    def test_ambiguous_lmstudio_settings_and_control_paths_refuse(self):
        self.initialized_home()
        settings = self.home / ".lmstudio/settings.json"
        for value in ("{", '{"downloadsFolder":""}', '{"downloadsFolder":"relative"}'):
            settings.write_text(value)
            with self.assertRaises((setup.SetupError, ValueError)):
                self.apply()
            self.assertEqual(self.calls, [])
        with self.assertRaises(setup.SetupError):
            setup.owned_path(self.home / "bad\npath", missing=True)

    def test_cli_failure_stops_without_followup_import_or_config(self):
        self.initialized_home()
        native = self.native
        def fail_pull(argv, **kwargs):
            if "pull" in argv:
                raise setup.SetupError("native pull failed")
            return native(argv, **kwargs)
        self.native = fail_pull
        with self.assertRaisesRegex(setup.SetupError, "pull failed"):
            self.apply()
        self.assertFalse(os.path.lexists(self.paths["friendly"]))
        self.assertFalse(self.paths["ini"].exists())

    def test_native_cli_output_and_failure_are_bounded(self):
        self.assertEqual(setup.run([sys.executable, "-c", "import sys; sys.stderr.write('x'*2000000); print('ok')"]), b"ok\n")
        with self.assertRaisesRegex(setup.SetupError, "stdout exceeds"):
            setup.run([sys.executable, "-c", "print('x'*1100000)"])
        with self.assertRaisesRegex(setup.SetupError, "exit 7"):
            setup.run([sys.executable, "-c", "raise SystemExit(7)"])
        with self.assertRaisesRegex(setup.SetupError, "timed out"):
            setup.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)

    def test_cli_symlink_finds_bundled_profile_without_home_writes(self):
        launcher = self.home / "linked helper"
        launcher.symlink_to(ROOT / "bin/local-ai-setup")
        before = tree(self.home)
        result = subprocess.run([sys.executable, "-B", str(launcher), "--check", "--json"],
                                env=self.env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "qwen3:4b-instruct-2507-q4_K_M")
        self.assertEqual(tree(self.home), before)

    def test_public_profile_is_data_only_and_exact_pin(self):
        profile = setup.load_profile()
        self.assertEqual(profile["blob_bytes"], 2497280480)
        self.assertEqual(profile["model"], "qwen3:4b-instruct-2507-q4_K_M")
        self.assertEqual(profile["license"], "Apache-2.0")
        self.assertEqual(profile["manifest_sha256"], "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0")
        self.assertEqual(profile["blob_sha256"], "85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9")
        text = json.dumps(profile)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("http", text)
        self.assertNotIn("localhost", text)

    def test_ini_preserves_other_sections_and_comments_byte_for_byte(self):
        data = b"; existing comment\n[other/model:Q4]\nmodel=/existing/model.gguf\n"
        friendly = self.home / "Models/LocalAI" / self.profile["filename"]
        changed = setup.ini_update(data, self.profile, friendly)
        self.assertTrue(changed.startswith(data))
        self.assertEqual(setup.ini_update(changed, self.profile, friendly), changed)
        self.assertIn(b"ctx-size=4096\ntemp=0.7\n", changed)

    def test_ini_conflicts_and_duplicate_sections_refuse(self):
        friendly = self.home / "Models/LocalAI" / self.profile["filename"]
        exact = setup.ini_update(b"", self.profile, friendly)
        for data in (exact + exact, exact.replace(b"ctx-size=4096", b"ctx-size=8192"), b"not an ini"):
            with self.subTest(data=data), self.assertRaises(setup.SetupError):
                setup.ini_update(data, self.profile, friendly)

    def test_non_loopback_or_credentialed_ollama_host_refuses(self):
        for host in ("https://example.invalid", "0.0.0.0:11434", "http://127.0.0.1/path",
                     "http://user:password@127.0.0.1", "http://127.0.0.1?query=yes"):
            with self.subTest(host=host), self.assertRaises(setup.SetupError):
                setup.loopback_endpoint({"OLLAMA_HOST": host})
        self.assertEqual(setup.loopback_endpoint({}), ("http://127.0.0.1:11434", 11434))

    def test_listener_rejects_wildcard_and_remote_bindings(self):
        for output in (b"p123\nn*:11434\n", b"p123\nn192.0.2.1:11434\n",
                       b"p123\nn127.0.0.1:11434\nn*:11434\n"):
            with patch.object(setup, "run", return_value=output), self.assertRaises(setup.SetupError):
                setup.require_loopback_listener(11434)
        with patch.object(setup, "run", return_value=b"p123\nn127.0.0.1:11434\nn[::1]:11434\n"):
            setup.require_loopback_listener(11434)


if __name__ == "__main__":
    unittest.main()
