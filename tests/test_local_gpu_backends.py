"""Local GPU backends on machines unlike the developer's (no NVIDIA, AMD/Intel,
hybrid laptops, several GPUs, missing/hung tools, RDP/VM, odd driver output)."""
import os
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vram_radar import local_gpu
from vram_radar.connectors import ConnectorFailure, parse_nvidia_smi_rows
from vram_radar.local_gpu import BackendUnavailable, combine_windows_counters, query_local_gpu
from vram_radar.models import ServerProfile

SERVER = ServerProfile.from_dict({"id": "local", "display_name": "Local GPU", "backend": "local"})
NVML_ROW = local_gpu._gpu_row("0", "GPU-1", "NVIDIA GeForce RTX 5060 Laptop GPU",
                              8 * 1024**3, 1 * 1024**3, 7 * 1024**3, 12.0, 45.0)


def unavailable(code):
    def raiser(*_args, **_kwargs):
        raise BackendUnavailable(code)
    return raiser


class Backends(unittest.TestCase):
    def setUp(self):
        local_gpu._NVML.update(hung=False)
        self.patches = [patch.object(local_gpu, "_nvml_query", unavailable("nvml_missing")),
                        patch.object(local_gpu, "_nvidia_smi_path", return_value=None),
                        patch.object(local_gpu, "_windows_query", unavailable("not_windows")),
                        patch.object(local_gpu, "_sysfs_query", unavailable("no_sysfs_gpu"))]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in self.patches])

    def test_nvml_first_without_child_process(self):
        with patch.object(local_gpu, "_nvml_query", return_value=[NVML_ROW]), \
             patch.object(local_gpu, "_run_bounded_process", side_effect=AssertionError("no smi")):
            snapshot = query_local_gpu(SERVER)
        self.assertEqual(snapshot["processes"]["source"], "local NVML")
        self.assertEqual(snapshot["total_vram_gib"], 8.0)
        self.assertEqual(snapshot["gpus"][0]["temperature_c"], 45.0)

    def test_nvml_missing_falls_back_to_smi_outside_path(self):
        rows = b"0, GPU-a, NVIDIA RTX A2000, 6138, 100, 6038, 3, 40\n"
        with patch.object(local_gpu, "_nvidia_smi_path", return_value=r"C:\Windows\System32\nvidia-smi.exe"), \
             patch.object(local_gpu, "_run_bounded_process", return_value=SimpleNamespace(
                 returncode=0, stdout=rows, stderr=b"", stdout_truncated=False)):
            snapshot = query_local_gpu(SERVER)
        self.assertEqual(snapshot["processes"]["source"], "local nvidia-smi")

    def test_hung_nvml_is_abandoned_and_not_retried(self):
        release = threading.Event()
        self.patches[0].stop()
        self.patches.pop(0)
        calls = []
        def hang():
            calls.append(1)
            release.wait(5)
            return [NVML_ROW]
        try:
            with patch.object(local_gpu, "_nvml_read", side_effect=hang):
                with self.assertRaises(BackendUnavailable) as caught:
                    local_gpu._nvml_query(timeout=0.05)
                self.assertEqual(caught.exception.code, "nvml_timeout")
                with self.assertRaises(BackendUnavailable):
                    local_gpu._nvml_query(timeout=0.05)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            local_gpu._NVML.update(hung=False)

    def test_smi_unsupported_fields_stay_unknown(self):
        for token in ("[N/A]", "[Not Supported]", "N/A", "[Unknown Error]"):
            with self.subTest(token=token):
                gpu = parse_nvidia_smi_rows(f"0, GPU-x, Quadro P1000, 4096, 10, 4086, {token}, {token}\n")[0]
                self.assertIsNone(gpu["utilization_percent"])
                self.assertIsNone(gpu["temperature_c"])

    def test_multiple_gpus_and_non_ascii_names(self):
        text = ("0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1000, 23564, 5, 40\n"
                "1, GPU-b, NVIDIA GeForce RTX 4090 (显卡二), 24564, 2000, 22564, 7, 41\n")
        gpus = parse_nvidia_smi_rows(text)
        self.assertEqual([g["gpu_index"] for g in gpus], ["0", "1"])
        self.assertIn("显卡二", gpus[1]["gpu_type"])

    def test_no_nvidia_uses_windows_counters(self):
        amd = local_gpu._gpu_row("0", "LUID-1", "AMD Radeon RX 7800 XT", 16 * 1024**3, 2 * 1024**3,
                                 14 * 1024**3, 9.0, None)
        with patch.object(local_gpu, "_windows_query", return_value=[amd]), \
             patch.object(local_gpu.os, "name", "nt"):
            snapshot = query_local_gpu(SERVER)
        self.assertEqual(snapshot["processes"]["source"], "local Windows GPU counters")
        self.assertIsNone(snapshot["gpus"][0]["temperature_c"])

    def test_smi_failure_still_falls_back_to_counters(self):
        amd = local_gpu._gpu_row("0", "LUID-1", "NVIDIA GeForce GTX 1650", 4 * 1024**3, 1024**3, 3 * 1024**3,
                                 1.0, None)
        with patch.object(local_gpu, "_nvidia_smi_path", return_value="nvidia-smi"), \
             patch.object(local_gpu, "_run_bounded_process", side_effect=subprocess.TimeoutExpired("nvidia-smi", 5)), \
             patch.object(local_gpu, "_windows_query", return_value=[amd]), \
             patch.object(local_gpu.os, "name", "nt"):
            snapshot = query_local_gpu(SERVER)
        self.assertEqual(snapshot["gpus"][0]["gpu_type"], "NVIDIA GeForce GTX 1650")

    def test_smi_timeout_reported_when_nothing_else_works(self):
        with patch.object(local_gpu, "_nvidia_smi_path", return_value="nvidia-smi"), \
             patch.object(local_gpu, "_run_bounded_process", side_effect=subprocess.TimeoutExpired("nvidia-smi", 5)):
            with self.assertRaises(ConnectorFailure) as caught:
                query_local_gpu(SERVER)
        self.assertEqual(caught.exception.code, "local_gpu_timeout")

    def test_vm_or_rdp_without_gpu_is_a_clear_error(self):
        with self.assertRaises(ConnectorFailure) as caught:
            query_local_gpu(SERVER)
        self.assertEqual(caught.exception.code, "local_gpu_missing")
        self.assertFalse(caught.exception.retryable)

    def test_linux_amdgpu_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            device = os.path.join(root, "card0", "device")
            os.makedirs(os.path.join(device, "hwmon", "hwmon3"))
            for name, value in [("mem_info_vram_total", 17163091968), ("mem_info_vram_used", 1073741824),
                                ("gpu_busy_percent", 17)]:
                with open(os.path.join(device, name), "w") as handle:
                    handle.write(f"{value}\n")
            with open(os.path.join(device, "hwmon", "hwmon3", "temp1_input"), "w") as handle:
                handle.write("51000\n")
            os.makedirs(os.path.join(root, "card1", "device"))  # e.g. iGPU without VRAM files
            self.patches[3].stop()
            self.patches.pop(3)
            gpus = local_gpu._sysfs_query(root)
        self.assertEqual(len(gpus), 1)
        self.assertEqual((gpus[0]["memory_used_gib"], gpus[0]["utilization_percent"], gpus[0]["temperature_c"]),
                         (1.0, 17.0, 51.0))


class WindowsCounters(unittest.TestCase):
    ADAPTERS = [
        {"luid": "00000000_0000d2b4", "name": "AMD Radeon RX 6600", "dedicated": 8 * 1024**3, "shared": 16 * 1024**3},
        {"luid": "00000000_0000a1b2", "name": "Intel(R) UHD Graphics", "dedicated": 128 * 1024**2,
         "shared": 16 * 1024**3},
        {"luid": "00000000_0000ffff", "name": "Detached GPU", "dedicated": 4 * 1024**3, "shared": 0},
    ]

    def test_dedicated_shared_and_engine_utilization(self):
        dedicated = {"luid_0x00000000_0x0000D2B4_phys_0": 2 * 1024**3,
                     "luid_0x00000000_0x0000A1B2_phys_0": 64 * 1024**2}
        shared = {"luid_0x00000000_0x0000A1B2_phys_0": 3 * 1024**3}
        engines = {
            "pid_10_luid_0x00000000_0x0000D2B4_phys_0_eng_0_engtype_3D": 30.0,
            "pid_11_luid_0x00000000_0x0000D2B4_phys_0_eng_0_engtype_3D": 25.0,
            "pid_12_luid_0x00000000_0x0000D2B4_phys_0_eng_3_engtype_VideoDecode": 40.0,
            "pid_13_luid_0x00000000_0x0000A1B2_phys_0_eng_0_engtype_3D": 250.0,
        }
        gpus = combine_windows_counters(self.ADAPTERS, dedicated, shared, engines)
        self.assertEqual(len(gpus), 2)  # adapter without counters is skipped, not invented
        amd, intel = gpus
        self.assertEqual((amd["memory_total_gib"], amd["memory_used_gib"], amd["utilization_percent"]),
                         (8.0, 2.0, 55.0))
        self.assertIn("共享显存", intel["gpu_type"])
        self.assertEqual((intel["memory_total_gib"], intel["memory_used_gib"]), (16.0, 3.0))
        self.assertEqual(intel["utilization_percent"], 100.0)

    def test_usage_above_total_is_clamped(self):
        gpus = combine_windows_counters(self.ADAPTERS[:1], {"luid_0x00000000_0x0000d2b4_phys_0": 9 * 1024**3}, {}, {})
        self.assertEqual(gpus[0]["memory_free_gib"], 0.0)
        self.assertIsNone(gpus[0]["utilization_percent"])


if __name__ == "__main__":
    unittest.main()
