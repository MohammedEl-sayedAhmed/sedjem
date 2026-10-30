"""Tests for the Windows release beyond the build itself (docs/10-desktop.md): the version resource of Sedjem.exe
(desktop/build.py) and of the installer; the installer's Privacy page and the choice it leaves beside Sedjem.exe for
the app (desktop/installer.iss, Config.apply_installer_choice); the Desktop build workflow's checks of both, its
update test of silent installations, the privacy policy on release pages and the winget job, which runs only for a
release once its secret exists; the winget manifests and the script that writes them (packaging/winget/); and the
privacy policy with what it lists and links. Offline; the workflow checks need PyYAML, as in tests/test_preview_plan.py.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import yaml
except ImportError:  # the workflow checks then skip
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app.config as config_module  # noqa: E402
from app.config import Config, installer_choice  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load("sedjem_build_windows", "desktop/build.py")
make = load("winget_make", "packaging/winget/make.py")
ISS = (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8")
CODE = ISS.split("\n[Code]\n")[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "desktop.yml").read_text(encoding="utf-8")
SHA_031 = "d4e1374575e5b9c9c9f6cc727ee51e753646526cd4975318289bb8258a31291c"  # Sedjem-0.3.1-setup.exe, as published


def steps(job):
    return {s.get("name") or s.get("uses"): s for s in job["steps"]}


class Recorded:
    """Stands in for one of PyInstaller's version resource classes: keeps what the text passes it."""

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def evaluate(text):
    """The version file evaluated as PyInstaller does, by eval with its classes in scope, here stand-ins for them.
    The text is desktop/build.py's own, and nothing else is in scope."""
    names = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")
    scope = {name: type(name, (Recorded,), {}) for name in names}
    return eval(compile(text, "version-info.txt", "eval"), {"__builtins__": {}}, scope)  # noqa: S307


def strings(info):
    """The version resource's strings, by name."""
    (table,) = info.kwargs["kids"][0].args[0]
    return dict(s.args for s in table.args[1])


# ---------------------------------------------------------------------------------------------
# Sedjem.exe's version resource
# ---------------------------------------------------------------------------------------------

class VersionResource(unittest.TestCase):
    def test_the_numbers_are_x_y_z_0(self):
        self.assertEqual(build.version_numbers("0.3.1"), (0, 3, 1, 0))
        self.assertEqual(build.version_numbers("0.3.1-preview.20261030"), (0, 3, 1, 0), "a preview's date doesn't fit")
        self.assertEqual(build.version_numbers("0.3.1-preview.20261030.2"), (0, 3, 1, 0))
        self.assertEqual(build.version_numbers("65535.0.65535"), (65535, 0, 65535, 0))
        for version in ("65536.0.0", "0.3.70000", "v0.3.1", ""):
            with self.subTest(version=version), self.assertRaises(ValueError):
                build.version_numbers(version)

    def test_the_text_names_the_product_its_version_and_the_author(self):
        info = evaluate(build.version_resource("0.3.1-preview.20261030"))
        ffi = info.kwargs["ffi"].kwargs
        self.assertEqual((ffi["filevers"], ffi["prodvers"]), ((0, 3, 1, 0), (0, 3, 1, 0)))
        self.assertEqual((ffi["fileType"], ffi["OS"]), (0x1, 0x40004), "an application, for Windows NT")
        strings_info, var_info = info.kwargs["kids"]
        self.assertEqual(strings_info.args[0][0].args[0], "040904B0", "US English, Unicode")
        self.assertEqual(strings(info), {
            "CompanyName": "Mohammed El-sayed Ahmed", "FileDescription": "Sedjem", "FileVersion": "0.3.1.0",
            "InternalName": "Sedjem", "LegalCopyright": "Copyright (C) 2026 Mohammed El-sayed Ahmed",
            "OriginalFilename": "Sedjem.exe", "ProductName": "Sedjem", "ProductVersion": "0.3.1-preview.20261030"})
        (translation,) = var_info.args[0]
        self.assertEqual(translation.args, ("Translation", [0x0409, 1200]))
        self.assertEqual(strings(evaluate(build.version_resource("0.3.1")))["ProductVersion"], "0.3.1",
                         "a release's own version, as its product version")

    def test_pyinstaller_reads_it(self):
        try:  # only on Windows, where PyInstaller has what this module imports
            from PyInstaller.utils.win32 import versioninfo
        except ImportError:
            self.skipTest("PyInstaller's version resource reader runs on Windows only")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "version-info.txt"
            path.write_text(build.version_resource("1.2.3-preview.20261030"), encoding="utf-8")
            info = versioninfo.load_version_info_from_text_file(str(path))
        self.assertEqual((info.ffi.fileVersionMS, info.ffi.fileVersionLS), (1 << 16 | 2, 3 << 16))
        self.assertEqual((info.ffi.productVersionMS, info.ffi.productVersionLS), (1 << 16 | 2, 3 << 16))
        self.assertTrue(info.toRaw())

    def run_build(self, platform, version="0.3.1-preview.20261030"):
        """build.main() as on the given system, PyInstaller replaced: (its options, the version file it was given)."""
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stamp_file = root / "app" / "_build.json"
            stamp_file.parent.mkdir()

            def run(opts):  # PyInstaller: reads the version file, and copies the stamp where a build has it
                seen["opts"] = opts
                if "--version-file" in opts:
                    seen["text"] = Path(opts[opts.index("--version-file") + 1]).read_text(encoding="utf-8")
                bundled = root / "dist" / "Sedjem" / "_internal" / "app"
                bundled.mkdir(parents=True)
                shutil.copy(stamp_file, bundled / "_build.json")

            pyinstaller = types.ModuleType("PyInstaller")
            pyinstaller.__main__ = types.SimpleNamespace(run=run)
            with mock.patch.object(build, "ROOT", root), mock.patch.object(build, "STAMP", stamp_file), \
                    mock.patch.object(build, "sys", types.SimpleNamespace(platform=platform, exit=sys.exit)), \
                    mock.patch.object(build, "git", lambda *args: None), mock.patch.object(sys, "argv", ["build.py"]), \
                    mock.patch.dict(sys.modules, {"PyInstaller": pyinstaller, "PyInstaller.__main__": pyinstaller.__main__}), \
                    mock.patch.dict(build.os.environ, {"SEDJEM_VERSION": version, "SEDJEM_CHANNEL": ""}), \
                    mock.patch("builtins.print"):
                build.main()
        return seen["opts"], seen.get("text")

    def test_the_build_passes_it_to_pyinstaller_on_windows(self):
        opts, text = self.run_build("win32")
        self.assertEqual(text, build.version_resource("0.3.1-preview.20261030"))
        self.assertTrue(opts[opts.index("--version-file") + 1].endswith(str(Path("build") / "version-info.txt")))
        opts, text = self.run_build("linux")
        self.assertNotIn("--version-file", opts, "only an .exe has a version resource")

    def test_a_version_an_exe_cant_hold_stops_the_build(self):
        with self.assertRaises(SystemExit) as stop:
            self.run_build("win32", version="0.3.70000")
        self.assertIn("65535", str(stop.exception.code))


# ---------------------------------------------------------------------------------------------
# The installer's Privacy page, and the choice it leaves for the app
# ---------------------------------------------------------------------------------------------

def left_by_setup(value, changed):
    """installer.json as LeaveUsageChoice writes it (the expression is checked in InstallerPage)."""
    as_json = {True: "true", False: "false"}
    return '{"usage_counts": ' + as_json[value] + ', "changed": ' + as_json[changed] + '}'


class InstallerPage(unittest.TestCase):
    def test_the_script_is_plain_ascii(self):
        ISS.encode("ascii")  # ISCC then reads it the same whatever encoding it assumes

    def test_the_page_comes_after_the_licence_with_its_link_and_box(self):
        self.assertIn("CreateCustomPage(wpLicense, 'Privacy',", CODE)
        self.assertIn("UsageBox.Caption := '&Send anonymous usage counts';", CODE)
        url = re.search(r"PrivacyPolicy = '([^']+)';", CODE).group(1)
        self.assertEqual(url, "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/privacy.md")
        self.assertTrue((ROOT / "docs" / "privacy.md").is_file())
        self.assertIn("ShellExecAsOriginalUser('open', Link,", CODE, "the link opens as the user, not as an admin")
        self.assertIn("UsageBox.Checked := not (UsageWasOff or UsageCountsOffParam);", CODE, "ticked by default")
        self.assertRegex(ISS, r"#if Ver < EncodeVer\(6, 3, 0\)\n  #error", "TNewLinkLabel needs Inno Setup 6.3")

    def test_a_silent_installation_leaves_the_setting_alone_unless_told(self):
        step = re.search(r"procedure CurStepChanged\(CurStep: TSetupStep\);\nbegin\n(.*?)\nend;", CODE, re.S).group(1)
        self.assertIn("if CurStep <> ssPostInstall then\n    exit;", step)
        self.assertRegex(step, r"if not WizardSilent then\n(\s*//.*\n)?\s*LeaveUsageChoice\(UsageBox\.Checked, "
                               r"UsageBox\.Checked = UsageWasOff\)\n\s*else if UsageCountsOffParam then\n\s*"
                               r"LeaveUsageChoice\(False, True\);$")
        self.assertIn("ExpandConstant('{param:USAGECOUNTS|1}') = '0'", CODE)
        self.assertEqual(CODE.count("LeaveUsageChoice("), 3, "defined once, called in those two places only")

    def test_the_choice_goes_beside_sedjem_exe_never_into_the_data_folder(self):
        self.assertIn("SaveStringToFile(ExpandConstant('{app}\\installer.json'),", CODE)
        self.assertIn("""'{"usage_counts": ' + JsonBool(Value) + ', "changed": ' + JsonBool(Changed) + '}'""", CODE)
        self.assertEqual(CODE.count("SaveStringToFile("), 1, "the only file setup writes itself")
        self.assertNotIn("ForceDirectories(", CODE, "so it never makes the data folder either")
        self.assertRegex(ISS, r'\[UninstallDelete\]\n(;.*\n)*Type: files; Name: "\{app\}\\installer\.json"\n',
                         "a choice Sedjem never took goes with the program")
        with tempfile.TemporaryDirectory() as d:  # where the app looks: beside the .exe that runs, whatever the data folder
            exe = Path(d) / "Sedjem.exe"
            self.assertEqual(installer_choice(frozen=True, executable=str(exe)), exe.resolve().parent / "installer.json")
        self.assertIsNone(installer_choice(frozen=False), "from source there is no installer")

    def test_the_box_starts_as_the_setting_is_where_the_app_keeps_it(self):
        config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        for name in ("SEDJEM_HOME", "TAFRIGH_HOME", "LOCALAPPDATA"):
            self.assertIn(f"'{name}'", CODE)
            self.assertIn(f'"{name}"', config)
        self.assertIn('"Sedjem", "Tafrigh"', config)
        self.assertIn("AddBackslash(Base) + 'Sedjem'", CODE)
        self.assertIn("AddBackslash(Base) + 'Tafrigh'", CODE)
        storage = tomllib.loads((ROOT / "app" / "config.toml").read_text(encoding="utf-8"))["storage"]["dir"]
        self.assertEqual(storage, "app_data")
        self.assertIn("LoadStringFromFile(AddBackslash(Folder) + 'app_data\\settings.json', Saved)", CODE)
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(home=d)
            self.assertEqual(cfg.storage, Path(d) / "app_data")
            for value in (True, False):  # what the page reads back is what Sedjem writes, without its spaces
                cfg.save_settings(usage_counts=value)
                squeezed = "".join(cfg.settings_path.read_text(encoding="utf-8").split())
                self.assertEqual('"usage_counts":false' in squeezed, value is False)
        self.assertIn("""Pos('"usage_counts":false', Settings) > 0""", CODE)
        for char in ("' '", "#9", "#13", "#10"):
            self.assertIn(f"StringChangeEx(Settings, {char}, '', True);", CODE)

    def test_setup_names_the_same_product_and_version_as_sedjem_exe(self):
        self.assertIn('#define NumericVersion Pos("-", AppVersion) ? Copy(AppVersion, 1, Pos("-", AppVersion) - 1) : AppVersion',
                      ISS)
        exe = strings(evaluate(build.version_resource("0.3.1")))
        self.assertEqual(dict(re.findall(r"^(VersionInfo\w+)=(.*)$", ISS, re.M)), {
            "VersionInfoProductName": exe["ProductName"], "VersionInfoProductTextVersion": "{#AppVersion}",
            "VersionInfoProductVersion": "{#NumericVersion}", "VersionInfoVersion": "{#NumericVersion}",
            "VersionInfoCompany": exe["CompanyName"], "VersionInfoDescription": "Sedjem Setup",
            "VersionInfoCopyright": exe["LegalCopyright"]})


class InstallerChoice(unittest.TestCase):
    """Config.apply_installer_choice: installer.json, beside Sedjem.exe, applied once on the desktop app's next start."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-installer-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.cfg = Config(home=self.home / "data")
        self.choice = self.home / "Programs" / "Sedjem" / "installer.json"  # beside Sedjem.exe

    def leave(self, text):
        self.choice.parent.mkdir(parents=True, exist_ok=True)
        self.choice.write_text(text, encoding="ascii")

    def test_the_page_decides_as_the_installer_shows_it(self):
        # (saved before, the box as the page showed it, the box when Next was clicked) -> saved after
        cases = [
            (None, True, True, True),     # a new installation, left ticked: the page was the notice
            (None, True, False, False),   # a new installation, unticked
            (True, True, True, True),     # installed again, nothing changed
            (True, True, False, False),   # installed again, unticked
            (False, False, False, False),  # turned off before: the page showed it so and nothing changed
            (False, False, True, True),   # turned off before, ticked again by hand
            (False, True, True, False),   # off, but shown ticked (a settings.json written by hand): never turned on
        ]
        for before, shown, box, after in cases:
            with self.subTest(before=before, shown=shown, box=box):
                if self.cfg.settings_path.exists():
                    self.cfg.settings_path.unlink()
                if before is not None:
                    self.cfg.save_settings(usage_counts=before)
                was_off = not shown  # UsageWasOff: the page shows the box unticked when the counts were off
                self.leave(left_by_setup(box, box == was_off))  # as CurStepChanged calls LeaveUsageChoice
                applied = self.cfg.apply_installer_choice(self.choice)
                self.assertEqual(self.cfg.setting("usage_counts"), after)
                self.assertEqual(applied, after if after != before else None)
                self.assertFalse(self.choice.exists(), "applied once, then gone")

    def test_the_other_settings_are_kept(self):
        self.cfg.save_settings(update_check=False, threads=1, usage_counts=True)
        self.leave(left_by_setup(False, True))  # /USAGECOUNTS=0, or the box unticked
        self.assertIs(self.cfg.apply_installer_choice(self.choice), False)
        self.assertEqual(json.loads(self.cfg.settings_path.read_text(encoding="utf-8")),
                         {"update_check": False, "threads": 1, "usage_counts": False})

    def test_it_is_applied_once(self):
        self.leave(left_by_setup(True, False))
        self.assertIs(self.cfg.apply_installer_choice(self.choice), True)
        self.cfg.save_settings(usage_counts=False)  # turned off later in Settings → About
        self.assertIsNone(self.cfg.apply_installer_choice(self.choice))
        self.assertIs(self.cfg.setting("usage_counts"), False)

    def test_without_a_choice_or_with_a_broken_one_nothing_changes(self):
        self.assertIsNone(self.cfg.apply_installer_choice(self.choice))
        self.assertIsNone(self.cfg.apply_installer_choice(), "from source it never looks")
        self.assertFalse(self.cfg.settings_path.exists(), "nothing written")
        for text in ("", "not json", "[]", '{"usage_counts": "no", "changed": true}', '{"changed": true}'):
            with self.subTest(text=text):
                self.leave(text)
                self.assertIsNone(self.cfg.apply_installer_choice(self.choice))
                self.assertFalse(self.choice.exists(), "dropped")
                self.assertIsNone(self.cfg.setting("usage_counts"))

    def test_in_program_files_it_only_turns_the_counts_off_for_users_who_havent_chosen(self):
        # an installation for all users: the file can't be deleted there, so it stays and is read at every start
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("Program Files")):
            for changed in (False, True):  # left ticked, or ticked by hand: no other user's notice is skipped
                self.leave(left_by_setup(True, changed))
                self.assertIsNone(self.cfg.apply_installer_choice(self.choice))
                self.assertIsNone(self.cfg.setting("usage_counts"))
            self.leave(left_by_setup(False, True))  # unticked
            self.assertIs(self.cfg.apply_installer_choice(self.choice), False)
            self.assertIsNone(self.cfg.apply_installer_choice(self.choice), "taken once: now there is a choice")
            self.cfg.save_settings(usage_counts=True)  # turned on again in Settings → About
            self.assertIsNone(self.cfg.apply_installer_choice(self.choice))
        self.assertIs(self.cfg.setting("usage_counts"), True)
        self.assertTrue(self.choice.exists())

    def test_two_users_of_an_installation_for_all_users(self):
        other = Config(home=self.home / "other-user")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("Program Files")):
            self.leave(left_by_setup(True, False))  # left ticked by whoever installed it
            for user in (self.cfg, other):
                self.assertIsNone(user.apply_installer_choice(self.choice))
                self.assertIsNone(user.setting("usage_counts"), "each still gets the first-start notice")
            other.save_settings(usage_counts=True)  # the second user answered the notice
            self.leave(left_by_setup(False, True))  # installed again, unticked
            self.assertIs(self.cfg.apply_installer_choice(self.choice), False)
            self.assertIsNone(other.apply_installer_choice(self.choice), "a choice already made stays")
        self.assertEqual((self.cfg.setting("usage_counts"), other.setting("usage_counts")), (False, True))

    def test_it_reaches_the_data_folder_that_app_config_finds(self):
        clean = {k: v for k, v in os.environ.items() if k not in ("SEDJEM_HOME", "TAFRIGH_HOME")}
        elsewhere = self.home / "elsewhere"
        with mock.patch.dict(os.environ, {**clean, "SEDJEM_HOME": str(elsewhere)}, clear=True):
            self.leave(left_by_setup(False, True))
            cfg = Config()  # the data folder as the app finds it, here SEDJEM_HOME's
            self.assertIs(cfg.apply_installer_choice(self.choice), False)
        self.assertEqual(json.loads((elsewhere / "app_data" / "settings.json").read_text(encoding="utf-8")),
                         {"usage_counts": False})
        if sys.platform == "darwin":
            return  # its data folder is under the home folder, which the test can't move
        # an installed copy that still has the folder of Sedjem's old name, which it renames when it starts
        old, new = ("Tafrigh", "Sedjem") if sys.platform == "win32" else ("tafrigh", "sedjem")
        base = self.home / "local"
        (base / old / "app_data").mkdir(parents=True)
        env = {**clean, "LOCALAPPDATA": str(base), "XDG_DATA_HOME": str(base)}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config_module, "FROZEN", True):
            self.leave(left_by_setup(False, True))
            cfg = Config()
            self.assertIs(cfg.apply_installer_choice(self.choice), False)
        self.assertEqual(cfg.storage, base / new / "app_data")
        self.assertFalse((base / old).exists())
        self.assertIs(json.loads((base / new / "app_data" / "settings.json").read_text(encoding="utf-8"))["usage_counts"],
                      False)

    def test_the_desktop_app_applies_it_before_it_starts_anything(self):
        source = (ROOT / "app" / "desktop.py").read_text(encoding="utf-8")
        main = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "main")
        body = ast.get_source_segment(source, main)
        start = body.index("cfg = Config()\n")
        self.assertRegex(body[start:], r"^cfg = Config\(\)\n\s+cfg\.apply_installer_choice\(\)")
        self.assertLess(start, body.index("make_server("))
        for other in ("if args.mcp:", "if args.self_test:"):  # an assistant's server and the self-test return before
            self.assertLess(body.index(other), start)


# ---------------------------------------------------------------------------------------------
# The Desktop build workflow
# ---------------------------------------------------------------------------------------------

@unittest.skipIf(yaml is None, "no PyYAML")
class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = yaml.safe_load(WORKFLOW)["jobs"]

    def test_only_a_published_release_goes_to_winget_once_its_secret_exists(self):
        version = self.jobs["version"]
        self.assertEqual(version["outputs"]["winget"], "${{ steps.winget.outputs.winget }}")
        step = steps(version)["winget"]
        self.assertEqual(step["id"], "winget")
        self.assertEqual(step["if"], "steps.version.outputs.publish == 'true' && steps.version.outputs.channel == 'stable'")
        self.assertEqual(step["env"], {"WINGET": "${{ secrets.WINGET_TOKEN != '' }}"}, "whether it exists, never its value")
        self.assertEqual(re.findall(r"secrets\.(\w+)", WORKFLOW), ["WINGET_TOKEN", "WINGET_TOKEN"],
                         "the token reaches only the winget job's one step")
        for job in self.jobs.values():
            self.assertNotIn("secrets", str(job.get("env", "")), "never in a whole job's environment")
        self.assertNotIn("signpath", WORKFLOW.lower())
        self.assertEqual((self.jobs["build"]["timeout-minutes"], self.jobs["build"].get("permissions")), (45, None),
                         "the build job as on main: the workflow's contents: read")

    def test_the_exe_and_the_installer_name_the_product_and_its_version(self):
        by_name = steps(self.jobs["build"])
        run = by_name["Version resource (Windows)"]["run"]
        self.assertIn("(Get-Item dist\\Sedjem\\Sedjem.exe).VersionInfo", run)
        self.assertIn('"$($info.ProductName)".Trim() -ne "Sedjem" -or "$($info.ProductVersion)".Trim() -ne $env:VERSION', run)
        step = by_name["Installer version resource (Windows)"]
        self.assertEqual(step["if"], "runner.os == 'Windows' && hashFiles('dist/Sedjem-*-setup.exe') != ''")
        self.assertIn('(Get-Item "dist\\Sedjem-${env:VERSION}-setup.exe").VersionInfo', step["run"])
        self.assertIn('"$($info.ProductName)".Trim() -ne "Sedjem" -or "$($info.ProductVersion)".Trim() -ne $env:VERSION', step["run"])

    def test_the_update_test_covers_silent_installations_and_the_switch(self):
        run = steps(self.jobs["build"])["Installer update mode (Windows)"]["run"]
        self.assertEqual(run.count('Assert-Untouched "'), 2, "after the silent installation and after the update")
        for part in ('$choice = Join-Path $dir "installer.json"', '"app_data\\settings.json"', '"/USAGECOUNTS=0"',
                     '$left.usage_counts -ne $false -or $left.changed -ne $true', 'Start-Process -FilePath $exe -ArgumentList "--server"',
                     "if (Test-Path $choice) { throw", "$now.usage_counts -ne $false -or $now.update_check -ne $false"):
            self.assertIn(part, run)

    def test_release_pages_end_with_the_privacy_policy(self):
        run = steps(self.jobs["release"])["Publish"]["run"]
        stable = run[run.index("\nelse\n"):]  # previews keep their own notes (preview_plan.py notes)
        self.assertIn('privacy="[Privacy policy](https://github.com/$GITHUB_REPOSITORY/blob/main/docs/privacy.md)"', stable)
        self.assertIn("""{ cat "$notes"; printf '\\n---\\n\\n%s\\n' "$privacy"; } > ../release-notes.md""", stable)
        # the notes written for the release, or a draft from its commits (.github/scripts/release.py)
        self.assertLess(stable.index('release.py notes "$VERSION"'), stable.index('cat "$notes"'))
        self.assertNotIn("code-signing", run.lower())

    def test_winget_gets_a_release_once_its_token_exists(self):
        job = self.jobs["winget"]
        self.assertEqual(job["needs"], ["version", "release"])
        self.assertEqual(job["if"], "needs.version.outputs.winget == 'true'")
        self.assertEqual((job["runs-on"], job["permissions"]), ("windows-latest", {"contents": "read"}))
        (step,) = job["steps"]
        self.assertEqual(step["env"]["WINGET_CREATE_GITHUB_TOKEN"], "${{ secrets.WINGET_TOKEN }}")
        run = step["run"]
        self.assertNotIn("--token", run, "the token never on a command line, where it could be logged")
        self.assertIn("$_.name -match '-setup\\.exe$'", run)
        self.assertIn(f"update {make.IDENTIFIER} --version $env:VERSION", run)
        self.assertIn('--urls "$($setup[0].url)|x64|user"', run)
        self.assertIn("--submit --no-open", run)
        url, digest = re.search(r'Invoke-WebRequest "(https://github\.com/microsoft/winget-create/releases/download/'
                                r'v[\d.]+/wingetcreate\.exe)".*?-ne "([0-9A-F]{64})"', run, re.S).groups()
        self.assertTrue(url and digest, "a fixed version of wingetcreate, checked against its hash")


# ---------------------------------------------------------------------------------------------
# winget: the manifests and packaging/winget/make.py
# ---------------------------------------------------------------------------------------------

class Winget(unittest.TestCase):
    FOLDER = ROOT / "packaging" / "winget"
    NAMES = {f"{make.IDENTIFIER}.yaml", f"{make.IDENTIFIER}.installer.yaml", f"{make.IDENTIFIER}.locale.en-US.yaml"}

    def versions(self):
        return sorted(p for p in self.FOLDER.iterdir() if p.is_dir() and not p.name.startswith("__"))

    def test_the_committed_manifests_are_what_make_writes(self):
        self.assertEqual(self.versions(), [self.FOLDER / "0.3.1"], "winget's first pull request carries the newest only")
        for folder in self.versions():
            with self.subTest(folder.name):
                self.assertEqual({p.name for p in folder.iterdir()}, self.NAMES)
                installer = (folder / f"{make.IDENTIFIER}.installer.yaml").read_text(encoding="utf-8")
                sha256 = re.search(r"InstallerSha256: (\w+)", installer).group(1)
                date = re.search(r"ReleaseDate: (\S+)", installer)
                files = make.manifests(folder.name, sha256, date and date.group(1))
                for name, text in files.items():
                    self.assertEqual((folder / name).read_text(encoding="utf-8"), text, name)
        installer = (self.FOLDER / "0.3.1" / f"{make.IDENTIFIER}.installer.yaml").read_text(encoding="utf-8")
        self.assertIn(f"InstallerSha256: {SHA_031.upper()}\n", installer)
        self.assertIn("ReleaseDate: 2026-10-29\n", installer)

    @unittest.skipIf(yaml is None, "no PyYAML")
    def test_the_fields_winget_needs(self):
        files = {name: yaml.load(text, Loader=yaml.BaseLoader) for name, text in make.manifests("0.3.1", SHA_031).items()}
        version = files[f"{make.IDENTIFIER}.yaml"]
        installer = files[f"{make.IDENTIFIER}.installer.yaml"]
        locale = files[f"{make.IDENTIFIER}.locale.en-US.yaml"]
        for manifest, kind in ((version, "version"), (installer, "installer"), (locale, "defaultLocale")):
            self.assertEqual((manifest["PackageIdentifier"], manifest["PackageVersion"]), (make.IDENTIFIER, "0.3.1"))
            self.assertEqual((manifest["ManifestType"], manifest["ManifestVersion"]), (kind, make.MANIFEST_VERSION))
        self.assertEqual(version["DefaultLocale"], locale["PackageLocale"])
        self.assertEqual({k: installer[k] for k in ("InstallerType", "Scope", "UpgradeBehavior")},
                         {"InstallerType": "inno", "Scope": "user", "UpgradeBehavior": "install"})
        self.assertEqual(installer["InstallerSwitches"]["Silent"], "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART")
        app_id = re.search(r"^AppId=\{(\{[0-9A-F-]+\})$", ISS, re.M).group(1)
        self.assertEqual(installer["ProductCode"], f"{app_id}_is1")
        self.assertIn(f"Uninstall\\{app_id}_is1", WORKFLOW, "the entry in Installed apps that the update test checks")
        (one,) = installer["Installers"]
        self.assertEqual(one, {"Architecture": "x64", "InstallerSha256": SHA_031.upper(),
                               "InstallerUrl": f"{make.REPO}/releases/download/v0.3.1/Sedjem-0.3.1-setup.exe"})
        self.assertEqual(locale["License"], "AGPL-3.0-only")
        self.assertEqual(locale["Publisher"], re.search(r"^AppPublisher=(.+)$", ISS, re.M).group(1),
                         "as Installed apps lists it")
        self.assertEqual(locale["PrivacyUrl"], f"{make.REPO}/blob/main/docs/privacy.md")
        self.assertEqual(locale["ReleaseNotesUrl"], f"{make.REPO}/releases/tag/v0.3.1")
        self.assertEqual(locale["Moniker"], "sedjem")
        self.assertNotIn("\n", locale["Description"], "one paragraph")
        # the limits of winget's schema 1.12.0
        self.assertRegex(make.IDENTIFIER, r'^[^\.\s\\/:\*\?"<>\|\x01-\x1f]{1,32}(\.[^\.\s\\/:\*\?"<>\|\x01-\x1f]{1,32}){1,7}$')
        self.assertLessEqual(len(locale["ShortDescription"]), 256)
        self.assertLessEqual(len(locale["Description"]), 10000)
        self.assertLessEqual(len(locale["Tags"]), 16)
        for tag in locale["Tags"]:
            self.assertRegex(tag, r"^[a-z0-9-]{1,40}$")

    @unittest.skipIf(yaml is None, "no PyYAML")
    def test_strings_read_back_as_written(self):
        for text in ("{6F3C2E9A}_is1", "a: b", "#c", "- d", "yes", "Copyright (C) 2026 Mohammed El-sayed Ahmed",
                     "Arabic–English", "https://example.com/a#b", "trailing: ", "x #y", "0.3.0", "2026-10-29"):
            with self.subTest(text=text):
                self.assertEqual(yaml.load(f"k: {make.scalar(text)}\n", Loader=yaml.BaseLoader)["k"], text)
        folded = make.lines({"Description": make.Folded("word " * 60 + "end.")})
        self.assertTrue(all(len(line) <= 100 for line in folded))
        self.assertEqual(yaml.load("\n".join(folded), Loader=yaml.BaseLoader)["Description"], "word " * 60 + "end.")

    def test_only_a_release_with_a_hash_and_a_date(self):
        for args in (("0.3.1-preview.20261030", SHA_031), ("v0.3.1", SHA_031), ("0.3.1", SHA_031[:-1]),
                     ("0.3.1", SHA_031, "29/10/2026")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                make.manifests(*args)
        self.assertNotIn("ReleaseDate", make.manifests("0.3.1", SHA_031)[f"{make.IDENTIFIER}.installer.yaml"])

    def test_the_script_writes_the_folder_and_can_hash_the_installer(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("builtins.print"):
            out = Path(d)
            make.main(["0.3.1", "--sha256", SHA_031, "--release-date", "2026-10-29", "--out", str(out)])
            for name in self.NAMES:
                self.assertEqual((out / "0.3.1" / name).read_text(encoding="utf-8"),
                                 (self.FOLDER / "0.3.1" / name).read_text(encoding="utf-8"))
            setup = out / "Sedjem-9.9.9-setup.exe"
            setup.write_bytes(b"not an installer")
            make.main(["9.9.9", "--installer", str(setup), "--out", str(out)])
            written = (out / "9.9.9" / f"{make.IDENTIFIER}.installer.yaml").read_text(encoding="utf-8")
            self.assertIn(hashlib.sha256(b"not an installer").hexdigest().upper(), written)
            with self.assertRaises(SystemExit):
                make.main(["9.9.9", "--sha256", "abc", "--out", str(out)])


# ---------------------------------------------------------------------------------------------
# The policies, and where they are linked
# ---------------------------------------------------------------------------------------------

class Policies(unittest.TestCase):
    @staticmethod
    def text(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    # Every third party Sedjem contacts, and its own privacy policy (each address was checked by hand on 2026-10-29)
    THIRD_PARTIES = {
        "Hugging Face": "https://huggingface.co/privacy",
        "GitHub": "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement",
        "files.pythonhosted.org": "https://policies.python.org/pypi.org/Privacy-Notice/",
        "YouTube": "https://policies.google.com/privacy",
        "Aptabase": "https://aptabase.com/legal/privacy",
        "ElevenLabs": "https://elevenlabs.io/privacy-policy",
        "Speechmatics": "https://www.speechmatics.com/legal/privacy-policy",
        "Google Gemini": "https://policies.google.com/privacy",
        "Deepgram": "https://deepgram.com/privacy",
        "AssemblyAI": "https://www.assemblyai.com/legal/privacy-policy",
        "Azure AI Speech": "https://www.microsoft.com/privacy/privacystatement",
        "OpenAI": "https://openai.com/policies/row-privacy-policy/",
        "Groq": "https://groq.com/privacy-policy",
        "Mistral": "https://legal.mistral.ai/terms/privacy-policy/",
    }

    def test_the_privacy_policy_names_everything_sedjem_contacts(self):
        policy = self.text("docs/privacy.md")
        for name, url in self.THIRD_PARTIES.items():
            with self.subTest(name):
                self.assertIn(name, policy)
                self.assertIn(f"]({url})", policy)
        for part in ("eu.aptabase.com", "api.github.com", "googlevideo.com", "(13-usage-counts.md)", "no account",
                     "/USAGECOUNTS=0", "DO_NOT_TRACK=1", "https://github.com/MohammedEl-sayedAhmed/sedjem/issues"):
            self.assertIn(part, policy)
        # a hosted service added to the app needs its row in the privacy policy
        config = tomllib.loads((ROOT / "app" / "config.toml").read_text(encoding="utf-8"))
        names = {"elevenlabs": "ElevenLabs", "speechmatics": "Speechmatics", "gemini": "Google Gemini",
                 "deepgram": "Deepgram", "assemblyai": "AssemblyAI", "azure": "Azure AI Speech", "openai": "OpenAI",
                 "groq": "Groq", "mistral": "Mistral"}
        self.assertEqual({m["id"] for m in config["models"] if m.get("kind") == "hosted"}, set(names))
        for name in names.values():
            self.assertRegex(policy, rf"\| {re.escape(name)} \| \[[^\]]+\]\({re.escape(self.THIRD_PARTIES[name])}\) \|")

    def test_uninstalling_is_described_for_each_system(self):
        notes = (ROOT / "docs" / "10-desktop.md").read_text(encoding="utf-8")
        section = re.search(r"^## Uninstall\n(.*?)(?=^## )", notes, re.S | re.M).group(1)
        for part in ("*Settings → Apps → Installed apps*", "*Uninstall*", "%LOCALAPPDATA%\\Sedjem",
                     "~/.local/share/applications/sedjem.desktop", "~/.local/share/sedjem", "From source"):
            self.assertIn(part, section)
        self.assertIn("[how to uninstall it](docs/10-desktop.md#uninstall)", self.text("README.md"))

    def test_it_is_linked_from_the_readme_the_docs_and_the_app(self):
        readme = self.text("README.md")
        self.assertIn("[privacy policy](docs/privacy.md)", readme)
        self.assertIn("[Privacy policy](docs/privacy.md)", readme)
        self.assertIn("once winget has accepted the package", readme)
        self.assertIn("winget install MohammedElsayedAhmed.Sedjem", readme)
        self.assertIn("[Privacy policy](privacy.md)", self.text("docs/README.md"))
        self.assertIn("[privacy policy](privacy.md)", self.text("docs/10-desktop.md"))
        about = re.search(r"function aboutPanel\(\) \{(.*?)\n\}", (ROOT / "app/static/app.js").read_text(encoding="utf-8"),
                          re.S).group(1)
        self.assertIn('href="https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/privacy.md"', about)

    def test_it_reads_plainly_and_claims_nothing_untested(self):
        # no bold and no em dashes, as in every doc: tests/test_public_text.py
        self.assertFalse((ROOT / "docs" / "code-signing.md").exists())
        for path in ("README.md", "docs/10-desktop.md", "docs/12-updates.md"):
            with self.subTest(path):
                prose = self.text(path)
                self.assertNotIn("code-signing.md", prose)
                self.assertNotIn("SignPath", prose)
                # a browser download of the unsigned installer warns, Sedjem's own updates don't, and nothing is said
                # of winget and SmartScreen, which nobody has tried
                self.assertRegex(prose, r"(isn't signed|unknown publisher|publisher is unknown)")
                self.assertNotRegex(prose, r"winget[^.]*SmartScreen|SmartScreen[^.]*winget")


if __name__ == "__main__":
    unittest.main()
