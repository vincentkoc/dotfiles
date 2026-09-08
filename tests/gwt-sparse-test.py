#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GIT = os.environ.get("GWT_TEST_GIT") or shutil.which("git")
ZSH = shutil.which("zsh")
SNAPSHOTS = (
    "apps/shared/OpenClawKit/Sources/OpenClawKit/Resources/tool-display.json",
    "apps/macos/Sources/OpenClaw/HostEnvSecurityPolicy.generated.swift",
)
INCLUDED = (
    "AGENTS.md", "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    ".agents/skills/review/SKILL.md", ".claude/settings.json", ".github/workflows/test.yml",
    ".vscode/settings.json", "config/models.json", "custodian-skills/check/SKILL.md",
    "deploy/container.yml", "docs/AGENTS.md", "docs/zh-CN/guide.md", "examples/demo.ts",
    "extensions/demo/package.json", "git-hooks/pre-commit", "packages/core/package.json",
    "patches/tool.patch", "qa/scenarios/test.yml", "qa/scores/baseline.json",
    "scripts/check.mjs", "security/policy.md", "skills/review/SKILL.md",
    "src/index.ts", "test/index.test.ts", "ui/AGENTS.md", "ui/scripts/check.ts",
    "ui/config/settings.json", "ui/src/index.ts", *SNAPSHOTS,
)
EXCLUDED = (
    "apps/ios/Sources/Other.swift", "apps/android/src/Other.kt",
    "apps/macos/Sources/OpenClaw/Other.swift",
    "apps/shared/OpenClawKit/Sources/OpenClawKit/Other.swift",
    "assets/large.bin", "vendor/a2ui/renderers/lit/index.ts", "bin/old",
    ".changeset/old.md", ".pi/old.md", "unrelated/large.bin",
)
SPECIAL_DIRS = ('"quoted dir', "line\nbreak", "-option")


class SparseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.fixture = self.root / "physical dotfiles"
        self.source = self.fixture / "functions/gwt/gwt.zsh"
        self.source.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "functions/gwt/gwt.zsh", self.source)
        shutil.copytree(ROOT / "git-sparse", self.fixture / "git-sparse")
        self.profiles = self.fixture / "git-sparse/openclaw-openclaw"
        (self.profiles / "cone.paths").write_text("src\nui\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "git.jsonl"
        shim = self.bin / "git"
        shim.write_text(f"#!{sys.executable}\n" + """
import json
import os
import sys
args = sys.argv[1:]
with open(os.environ["GWT_TEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
operation = args[2:] if args[:1] == ["-C"] else args
failure = os.environ.get("GWT_TEST_FAIL", "")
fail = (
    operation[:2] == ["sparse-checkout", failure]
    or failure == "state" and operation == ["config", "--bool", "core.sparseCheckout"]
    or failure == "profile" and operation[:3] == ["config", "--worktree", "dotfiles.sparseProfile"]
    or failure == "profile-file" and operation[:3] == ["config", "--worktree", "dotfiles.sparseProfileFile"]
    or failure == "unset" and operation[:3] == ["config", "--worktree", "--unset-all"]
)
if failure and fail:
    print("injected git failure: " + failure, file=sys.stderr)
    sys.exit(4)
os.execv(os.environ["GWT_TEST_REAL_GIT"], [os.environ["GWT_TEST_REAL_GIT"], *args])
""")
        shim.chmod(0o755)
        self.env = {
            "HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1", "GWT_TEST_REAL_GIT": str(GIT),
            "GWT_TEST_LOG": str(self.log), "LC_ALL": "C",
        }
        self.repo = self.root / "repo with spaces"
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.root)
        self.git("config", "user.name", "Sparse Fixture")
        self.git("config", "user.email", "sparse@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "gc.auto", "0")
        self.git("config", "maintenance.auto", "false")
        self.git("remote", "add", "origin", "https://github.com/openclaw/openclaw.git")
        for name in (*INCLUDED, *EXCLUDED, ".gitignore", "space dir/file.txt",
                     *(f"{directory}/file.txt" for directory in SPECIAL_DIRS)):
            file = self.repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("*.ignored\n" if name == ".gitignore" else f"{name}\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run([str(GIT), *map(str, args)], cwd=cwd or self.repo,
                                env=self.env, capture_output=True, check=check)
        return result.stdout

    def shell(self, script, *args, source=None, env=None, ok=True):
        result = subprocess.run(
            [str(ZSH), "-f", "-c", 'source "$1"\nshift\n' + script, "zsh",
             str(source or self.source), *map(str, args)],
            cwd=self.repo, env=dict(self.env, **(env or {})), capture_output=True, text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def sparse(self, *args, repo=None, env=None, ok=True):
        return self.shell('cd "$1"\nshift\ngwt sparse "$@"', repo or self.repo, *args,
                          env=env, ok=ok)

    def operations(self):
        if not self.log.exists():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [call[2:] if call[:1] == ["-C"] else call for call in calls]

    def snapshot(self, repo=None):
        repo = repo or self.repo
        return {str(file.relative_to(repo)): hashlib.sha256(file.read_bytes()).hexdigest()
                for file in repo.rglob("*") if file.is_file()}

    def config(self, key, repo=None):
        return self.git("config", "--get", key, cwd=repo, check=False).decode().strip()

    def test_physical_profile_authority_and_override(self):
        expected = str(self.fixture / "git-sparse") + "\n"
        self.assertEqual(self.shell("_gwt_sparse_root").stdout, expected)
        alias = self.root / "directory alias"
        alias.symlink_to(self.fixture, target_is_directory=True)
        self.assertEqual(self.shell("_gwt_sparse_root", source=alias / "functions/gwt/gwt.zsh").stdout,
                         expected)
        link = self.root / "relative-source.zsh"
        link.symlink_to(Path("physical dotfiles/functions/gwt/gwt.zsh"))
        self.assertEqual(self.shell("_gwt_sparse_root", source=link).stdout, expected)
        override = str(self.root / "operator override")
        self.assertEqual(self.shell("_gwt_sparse_root", env={"DOTFILES_GIT_SPARSE_ROOT": override}).stdout,
                         override + "\n")
        self.assertEqual(self.shell("_gwt_sparse_root", env={"DOTFILES_GIT_SPARSE_ROOT": ""}).stdout,
                         expected)

    def test_exports_does_not_invent_or_replace_override(self):
        for value in (None, str(self.root / "operator override")):
            env = dict(self.env)
            if value is not None:
                env["DOTFILES_GIT_SPARSE_ROOT"] = value
            result = subprocess.run(
                ["bash", "-c", 'source "$1"\nprintf "%s\\n" "${DOTFILES_GIT_SPARSE_ROOT-unset}"',
                 "bash", str(ROOT / ".exports")], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, (value or "unset") + "\n")

    def test_openclaw_profile_uses_native_git_membership(self):
        self.sparse("set", "core")
        for name in INCLUDED:
            self.assertTrue((self.repo / name).is_file(), name)
        for name in EXCLUDED:
            self.assertFalse((self.repo / name).exists(), name)
        self.assertEqual(self.config("core.sparseCheckoutCone"), "false")
        self.assertEqual(self.config("index.sparse"), "false")
        self.assertEqual(self.config("dotfiles.sparseProfile"), "core")
        self.assertEqual(self.config("dotfiles.sparseProfileFile"), str(self.profiles / "core.patterns"))
        self.assertEqual([op for op in self.operations() if op[0] == "sparse-checkout"],
                         [["sparse-checkout", "set", "--no-cone", "--no-sparse-index", "--stdin"]])

    def test_clawhub_profile_keeps_agent_guides_excludes_archive(self):
        self.git("remote", "set-url", "origin", "https://github.com/openclaw/clawhub.git")
        self.sparse("set", "core")
        self.assertTrue((self.repo / ".agents/skills/review/SKILL.md").is_file())
        self.assertTrue((self.repo / "src/index.ts").is_file())
        self.assertFalse((self.repo / "skills/review/SKILL.md").exists())

    def test_skills_profile_keeps_metadata_excludes_archive(self):
        self.git("remote", "set-url", "origin", "https://github.com/openclaw/skills.git")
        self.sparse("set", "core")
        self.assertTrue((self.repo / "AGENTS.md").is_file())
        self.assertTrue((self.repo / ".agents/skills/review/SKILL.md").is_file())
        self.assertFalse((self.repo / "skills/review/SKILL.md").exists())
        self.assertEqual(self.shell("_gwt_sparse_clone_filter openclaw-skills").stdout, "blob:none\n")
        self.assertEqual(self.shell('_gwt_sparse_default_profile "$PWD"').stdout, "core\n")

    def test_missing_profile_has_no_side_effects(self):
        before = self.snapshot()
        result = self.sparse("set", "missing", ok=False)
        self.assertIn("not found", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(op[0] == "sparse-checkout" for op in self.operations()))

    def test_cone_noncone_add_full_preserve_included_files_and_sibling(self):
        sibling = self.root / "sibling"
        self.git("worktree", "add", "-q", "-b", "sibling", str(sibling))
        sibling_before = self.snapshot(sibling)
        common_before = self.git("config", "--local", "--null", "--list")
        (self.repo / "src/index.ts").write_text("dirty tracked\n")
        (self.repo / "src/untracked.txt").write_text("untracked\n")
        (self.repo / "src/cache.ignored").write_text("ignored\n")
        preserved = {name: (self.repo / name).read_bytes() for name in (
            "src/index.ts", "src/untracked.txt", "src/cache.ignored")}
        for action in (("set", "cone"), ("set", "core"), ("add", "/space dir/"),
                       ("full",), ("set", "core")):
            self.sparse(*action)
            for name, data in preserved.items():
                self.assertEqual((self.repo / name).read_bytes(), data)
            self.assertEqual(self.snapshot(sibling), sibling_before)
            self.assertEqual(self.config("core.sparseCheckout", sibling), "")
        self.assertEqual(self.config("extensions.worktreeConfig"), "true")
        after = self.git("config", "--local", "--null", "--list")
        self.assertEqual(after.replace(b"extensions.worktreeconfig\ntrue\0", b""), common_before)
        self.assertFalse(any(op[:2] == ["sparse-checkout", "init"] for op in self.operations()))

    def test_add_preserves_existing_mode_and_full_add_uses_one_set(self):
        self.sparse("set", "cone")
        self.assertEqual(self.config("core.sparseCheckoutCone"), "true")
        self.assertEqual(self.config("index.sparse"), "true")
        self.sparse("add", "space dir")
        self.assertTrue((self.repo / "space dir/file.txt").is_file())
        self.assertEqual(self.config("core.sparseCheckoutCone"), "true")
        self.assertEqual(self.config("index.sparse"), "true")
        self.sparse("set", "core")
        self.sparse("add", "/space dir/")
        self.assertEqual(self.config("core.sparseCheckoutCone"), "false")
        self.assertEqual(self.config("index.sparse"), "false")
        self.assertTrue((self.repo / "space dir/file.txt").is_file())
        self.assertEqual(self.config("dotfiles.sparseProfile"), "custom")
        self.assertEqual(self.config("dotfiles.sparseProfileFile"), "")
        self.sparse("full")
        self.assertEqual(self.config("core.sparseCheckout"), "false")
        self.log.write_text("")
        self.sparse("add", "src", "space dir")
        self.assertEqual([op for op in self.operations() if op[0] == "sparse-checkout"],
                         [["sparse-checkout", "set", "--cone", "--sparse-index", "--", "src", "space dir"]])
        self.assertTrue((self.repo / "space dir/file.txt").is_file())

    def test_add_preserves_quoted_newline_and_option_like_argv(self):
        for existing in (False, True):
            if existing:
                self.sparse("set", "cone")
            self.log.write_text("")
            self.sparse("add", *SPECIAL_DIRS)
            for directory in SPECIAL_DIRS:
                self.assertTrue((self.repo / directory / "file.txt").is_file(), directory)
            operation = (["add"] if existing else ["set", "--cone", "--sparse-index"])
            self.assertEqual([op for op in self.operations() if op[0] == "sparse-checkout"],
                             [["sparse-checkout", *operation, "--", *SPECIAL_DIRS]])

    def test_fresh_add_and_full(self):
        self.sparse("add", "src")
        self.assertEqual(self.config("dotfiles.sparseProfile"), "custom")
        self.assertEqual(self.config("extensions.worktreeConfig"), "true")
        self.sparse("full")
        self.sparse("full")
        self.assertEqual(self.config("dotfiles.sparseProfile"), "full")

    def test_full_on_fresh_repository(self):
        self.sparse("full")
        self.assertEqual(self.config("dotfiles.sparseProfile"), "full")
        self.assertEqual(self.git("config", "--local", "--get", "dotfiles.sparseProfile",
                                  check=False), b"")

    def test_native_migrates_main_worktree_config_before_linked_apply(self):
        self.git("config", "core.worktree", str(self.repo))
        sibling = self.root / "linked"
        self.git("worktree", "add", "-q", "-b", "linked", str(sibling))
        self.sparse("set", "core", repo=sibling)
        self.assertEqual(self.git("config", "--local", "--get", "core.worktree", check=False), b"")
        self.assertEqual(self.git("config", "--local", "--get", "core.bare"), b"false\n")
        self.assertEqual(self.config("core.worktree"), str(self.repo))
        self.assertEqual(self.config("core.sparseCheckout"), "")
        self.assertEqual(self.config("core.sparseCheckout", sibling), "true")
        self.assertTrue((self.repo / EXCLUDED[0]).is_file())
        self.assertFalse((sibling / EXCLUDED[0]).exists())

    def test_native_migrates_bare_owner_config_before_linked_apply(self):
        bare = self.root / "bare.git"
        self.git("clone", "-q", "--bare", str(self.repo), str(bare), cwd=self.root)
        self.git("remote", "set-url", "origin", "https://github.com/openclaw/openclaw.git", cwd=bare)
        sibling = self.root / "bare linked"
        self.git("worktree", "add", "-q", "-b", "linked", str(sibling), cwd=bare)
        self.sparse("set", "core", repo=sibling)
        self.assertEqual(self.git("config", "--local", "--get", "core.bare", cwd=bare, check=False), b"")
        self.assertEqual(self.config("core.bare", bare), "true")
        self.assertEqual(self.config("core.bare", sibling), "")
        self.assertEqual(self.config("core.sparseCheckout", sibling), "true")
        self.assertFalse((sibling / EXCLUDED[0]).exists())

    def test_native_set_failure_does_not_write_metadata_or_initialize(self):
        before = self.snapshot()
        result = self.sparse("set", "core", env={"GWT_TEST_FAIL": "set"}, ok=False)
        self.assertIn("injected git failure", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_native_disable_and_add_failures_preserve_metadata(self):
        self.sparse("set", "core")
        for failure, args in (("disable", ("full",)), ("add", ("add", "/space dir/"))):
            before = self.snapshot()
            result = self.sparse(*args, env={"GWT_TEST_FAIL": failure}, ok=False)
            self.assertIn("injected git failure", result.stderr)
            self.assertEqual(self.snapshot(), before)

    def test_config_read_error_is_not_an_absent_setting(self):
        before = self.snapshot()
        result = self.sparse("add", "src", env={"GWT_TEST_FAIL": "state"}, ok=False)
        self.assertIn("cannot read sparse-checkout state", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(op[0] == "sparse-checkout" for op in self.operations()))

    def test_metadata_failures_report_native_apply_already_happened(self):
        for failure in ("profile", "profile-file"):
            result = self.sparse("set", "core", env={"GWT_TEST_FAIL": failure}, ok=False)
            self.assertIn("sparse-checkout succeeded, but profile metadata update failed", result.stderr)
            self.assertFalse((self.repo / EXCLUDED[0]).exists())
        self.sparse("set", "core")
        result = self.sparse("full", env={"GWT_TEST_FAIL": "unset"}, ok=False)
        self.assertIn("sparse-checkout succeeded, but profile metadata update failed", result.stderr)
        self.assertTrue((self.repo / EXCLUDED[0]).is_file())
        self.assertEqual(self.config("core.sparseCheckout"), "false")
        self.sparse("set", "core")
        result = self.sparse("add", "/space dir/", env={"GWT_TEST_FAIL": "profile"}, ok=False)
        self.assertIn("sparse-checkout succeeded, but profile metadata update failed", result.stderr)
        self.assertTrue((self.repo / "space dir/file.txt").is_file())


if __name__ == "__main__":
    unittest.main()
