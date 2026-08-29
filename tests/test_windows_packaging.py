from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WindowsPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = (ROOT / "packaging" / "vram-radar.iss").read_text(encoding="utf-8")
        cls.build_script = (ROOT / "Build-VramRadar-Installer.ps1").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.guide = (ROOT / "docs" / "windows-install-and-update.md").read_text(encoding="utf-8")

    def test_upgrade_identity_and_install_target_are_version_independent(self) -> None:
        self.assertIn("AppId={{1B2F9822-D7AF-47E9-9757-72F98DB2C106}", self.manifest)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\VRAM Radar", self.manifest)
        self.assertNotIn("MyAppVersion}", self.manifest.split("DefaultDirName=", 1)[1].splitlines()[0])
        self.assertIn("UsePreviousAppDir=yes", self.manifest)
        self.assertIn("UsePreviousTasks=yes", self.manifest)

    def test_shortcuts_keep_a_stable_executable_and_shell_identity(self) -> None:
        icon_lines = [
            line for line in self.manifest.splitlines()
            if line.startswith("Name:") and "Filename:" in line
        ]
        self.assertEqual(len(icon_lines), 2)
        for line in icon_lines:
            self.assertIn('Filename: "{app}\\{#MyAppExeName}"', line)
            self.assertIn('WorkingDir: "{app}"', line)
            self.assertIn('AppUserModelID: "VRAMRadar.Desktop"', line)
            self.assertNotIn("MyAppVersion", line)

    def test_upgrade_cleanup_is_scoped_to_replaceable_bundle_files(self) -> None:
        self.assertIn("[InstallDelete]", self.manifest)
        self.assertIn('Name: "{app}\\_internal"', self.manifest)
        self.assertIn('Name: "{app}\\VRAMRadar.exe"', self.manifest)
        self.assertIn('Name: "{app}\\VRAMRadarAskPass.exe"', self.manifest)
        self.assertIn('Name: "{app}\\VRAMRadarUpdater.exe"', self.manifest)
        self.assertNotIn('Name: "{app}\\*"', self.manifest)
        self.assertIn("CloseApplications=yes", self.manifest)
        self.assertIn("RestartApplications=yes", self.manifest)
        self.assertIn("function PrepareToInstall", self.manifest)
        self.assertIn("--quit-existing", self.manifest)
        self.assertIn("ewWaitUntilTerminated", self.manifest)
        self.assertIn("installed-marker.txt", self.manifest)
        self.assertIn(".vram-radar-installed", self.manifest)

    def test_installer_preflights_custom_directory_permissions(self) -> None:
        self.assertIn("function VerifyInstallDirectory", self.manifest)
        self.assertIn("ForceDirectories(InstallDirectory)", self.manifest)
        self.assertIn("SaveStringToFile(ProbeFile", self.manifest)
        self.assertIn("DeleteFile(ProbeFile)", self.manifest)
        self.assertIn("D:\\Apps\\VRAM Radar", self.manifest)
        self.assertIn("run it as administrator", self.manifest)
        self.assertLess(
            self.manifest.index("VerifyInstallDirectory(Result)"),
            self.manifest.index("--quit-existing"),
        )

    def test_windows_bundle_contains_notification_area_lifecycle(self) -> None:
        tray_source = (ROOT / "src" / "vram_radar" / "tray.py").read_text(encoding="utf-8")
        shell_source = (ROOT / "src" / "vram_radar" / "shell.py").read_text(encoding="utf-8")
        spec = (ROOT / "packaging" / "vram-radar.spec").read_text(encoding="utf-8")
        self.assertIn('MenuItem("显示 VRAM Radar"', tray_source)
        self.assertIn('MenuItem("退出"', tray_source)
        self.assertIn("events.minimized", tray_source)
        self.assertIn("events.closing", tray_source)
        self.assertIn("WindowsTrayController", shell_source)
        self.assertIn('sys.platform == "win32"', shell_source)
        self.assertIn('"pystray._win32"', spec)
        validator = (ROOT / "tools" / "validate_packaged_tray.py").read_text(encoding="utf-8")
        self.assertIn("SC_MINIMIZE", validator)
        self.assertIn("WM_CLOSE", validator)
        self.assertIn('"--quit-existing"', validator)
        self.assertIn('"--no-auto-import"', validator)

    def test_installer_builder_uses_source_version_and_inno_setup(self) -> None:
        self.assertIn("src\\vram_radar\\__init__.py", self.build_script)
        self.assertIn("Get-Command ISCC.exe", self.build_script)
        self.assertIn("Programs\\Inno Setup 6\\ISCC.exe", self.build_script)
        self.assertIn('"/DMyAppVersion=$Version"', self.build_script)
        self.assertIn("VRAMRadar-Setup-$Version.exe", self.build_script)

    def test_user_docs_require_the_stable_installer_update_path(self) -> None:
        readme_flat = " ".join(self.readme.split())
        for text in (
            "recommended download",
            "preserves the Start-menu or desktop shortcut",
            "public Release no longer offers a Windows portable ZIP",
        ):
            self.assertIn(text, readme_flat)
        guide_flat = " ".join(self.guide.split())
        self.assertIn("The shortcut therefore does not contain a release number", guide_flat)
        self.assertIn("SHA-256", guide_flat)
        self.assertIn("automatically restarts", guide_flat)
        self.assertIn("D:\\Apps\\VRAM Radar", guide_flat)
        self.assertIn("Program Files", guide_flat)


if __name__ == "__main__":
    unittest.main()
