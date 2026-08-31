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
        cls.signing_workflow = (ROOT / ".github" / "workflows" / "signpath-windows-validation.yml").read_text(encoding="utf-8")
        cls.signature_validator = (ROOT / "tools" / "validate_windows_signatures.ps1").read_text(encoding="utf-8")

    def test_upgrade_identity_and_install_target_are_version_independent(self) -> None:
        self.assertIn("AppId={{1B2F9822-D7AF-47E9-9757-72F98DB2C106}", self.manifest)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\VRAM Radar", self.manifest)
        self.assertNotIn("MyAppVersion}", self.manifest.split("DefaultDirName=", 1)[1].splitlines()[0])
        self.assertIn("UsePreviousAppDir=yes", self.manifest)
        self.assertIn("UsePreviousTasks=yes", self.manifest)
        self.assertIn("DisableDirPage=no", self.manifest)
        self.assertIn("D:\\Download\\VRAM Radar", self.guide)
        self.assertIn("does not change the Profile or OpenSSH trust location", self.guide)

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
        self.assertIn('Name: "{group}\\VRAM Radar.lnk"; Check: not IsValidationInstall', self.manifest)
        self.assertIn('Name: "{autodesktop}\\VRAM Radar.lnk"; Check: not IsValidationInstall', self.manifest)

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
        self.assertIn("if ResultCode <> 0 then", self.manifest)
        self.assertIn("still running, so Setup stopped before replacing", self.manifest)
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

    def test_postinstall_launch_uses_original_user_ssh_context(self) -> None:
        run_line = next(
            line for line in self.manifest.splitlines()
            if line.startswith('Filename: "{app}\\{#MyAppExeName}"')
        )
        self.assertIn('WorkingDir: "{app}"', run_line)
        self.assertIn("runasoriginaluser", run_line)
        self.assertIn("postinstall", run_line)
        self.assertIn("skipifsilent", run_line)

    def test_packaged_update_validation_cannot_replace_user_install_registration(self) -> None:
        validator = (ROOT / "tools" / "validate_packaged_update.py").read_text(encoding="utf-8")
        helper = (ROOT / "src" / "vram_radar" / "update_helper.py").read_text(encoding="utf-8")
        self.assertIn("Uninstallable=not IsValidationInstall", self.manifest)
        self.assertIn("CreateUninstallRegKey=not IsValidationInstall", self.manifest)
        self.assertIn("/VRAMRADARVALIDATION=1", validator)
        self.assertIn('"validation_mode": True', validator)
        self.assertIn('command.append("/VRAMRADARVALIDATION=1")', helper)
        self.assertIn('manual_environment["VRAM_RADAR_HOME"]', validator)
        self.assertIn("same-version packaged reinstall", validator)
        self.assertIn("wait_for_process_exit(manual_process.pid)", validator)

    def test_windows_bundle_contains_notification_area_lifecycle(self) -> None:
        tray_source = (ROOT / "src" / "vram_radar" / "tray.py").read_text(encoding="utf-8")
        shell_source = (ROOT / "src" / "vram_radar" / "shell.py").read_text(encoding="utf-8")
        spec = (ROOT / "packaging" / "vram-radar.spec").read_text(encoding="utf-8")
        self.assertIn('menu_label("显示 VRAM Radar", "Show VRAM Radar")', tray_source)
        self.assertIn('menu_label("退出", "Exit")', tray_source)
        self.assertIn("events.minimized", tray_source)
        self.assertIn("events.closing", tray_source)
        self.assertIn("WindowsTrayController", shell_source)
        self.assertIn('sys.platform == "win32"', shell_source)
        self.assertIn('"pystray._win32"', spec)
        validator = (ROOT / "tools" / "validate_packaged_tray.py").read_text(encoding="utf-8")
        self.assertIn("SC_MINIMIZE", validator)
        self.assertIn("WM_CLOSE", validator)
        self.assertIn("HWND_TOPMOST", validator)
        self.assertIn("HWND_NOTOPMOST", validator)
        self.assertIn('title.value == "VRAM Radar"', validator)
        self.assertIn('"--quit-existing"', validator)
        self.assertIn('"--no-auto-import"', validator)

    def test_installer_builder_uses_source_version_and_inno_setup(self) -> None:
        self.assertIn("src\\vram_radar\\__init__.py", self.build_script)
        self.assertIn("Get-Command ISCC.exe", self.build_script)
        self.assertIn("Programs\\Inno Setup 6\\ISCC.exe", self.build_script)
        self.assertIn('"/DMyAppVersion=$Version"', self.build_script)
        self.assertIn("VRAMRadar-Setup-$Version.exe", self.build_script)
        spec = (ROOT / "packaging" / "vram-radar.spec").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GITHUB_SHA"', spec)
        self.assertIn('"source_commit": source_commit', spec)

    def test_mit_license_is_shipped_and_presented_by_setup(self) -> None:
        spec = (ROOT / "packaging" / "vram-radar.spec").read_text(encoding="utf-8")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn('project_root / "LICENSE"', spec)
        self.assertIn("LicenseFile=..\\LICENSE", self.manifest)

    def test_signpath_validation_is_separate_and_fails_closed(self) -> None:
        for required in (
            "SIGNPATH_API_TOKEN",
            "SIGNPATH_ORGANIZATION_ID",
            "SIGNPATH_PROJECT_SLUG",
            "SIGNPATH_SIGNING_POLICY_SLUG",
            "SIGNPATH_BUNDLE_ARTIFACT_CONFIGURATION_SLUG",
            "SIGNPATH_INSTALLER_ARTIFACT_CONFIGURATION_SLUG",
        ):
            self.assertIn(required, self.signing_workflow)
        self.assertEqual(self.signing_workflow.count("SignPath/github-action-submit-signing-request@"), 2)
        self.assertIn("Build, sign, and verify Windows x64", self.signing_workflow)
        self.assertNotIn("release create", self.signing_workflow)
        self.assertIn("Get-AuthenticodeSignature", self.signature_validator)
        self.assertIn("TimeStamperCertificate", self.signature_validator)
        self.assertIn("SignPath Foundation", self.signature_validator)

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
