import unittest
import time
from unittest.mock import Mock
from vram_radar.models import Profile
from vram_radar.service import DashboardService
from vram_radar.connectors import ConnectorFailure

class RetryCadenceTests(unittest.TestCase):
    def service(self, interval=60):
        profile = Profile.from_dict({"schema_version": 1, "id": "test", "display_name": "Test", "refresh_seconds": interval, "servers": [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "test.invalid"}]})
        cache = Mock()
        cache.load.return_value = None
        return DashboardService(profile, cache, clock=lambda: 100)

    def test_failures_never_poll_faster_than_normal_interval(self):
        for interval in (15, 60, 300):
            service = self.service(interval)
            for backoff in (15, 30, 60, 120, 300, 300):
                service._record_failure("gpu", ConnectorFailure("proxy_failed", "offline", retryable=True))
                self.assertEqual(service.states["gpu"].next_attempt_monotonic, 100 + max(interval, backoff))
                self.assertFalse(service._due(service.states["gpu"], force=False))

    def test_server_requested_delay_still_respected(self):
        service = self.service()
        service._record_failure("gpu", ConnectorFailure("proxy_failed", "offline", retryable=True, retry_after_seconds=900))
        self.assertEqual(service.states["gpu"].next_attempt_monotonic, 1000)

    def test_nonretryable_failure_remains_stopped(self):
        service = self.service()
        service._record_failure("gpu", ConnectorFailure("auth_failed", "denied", retryable=False))
        self.assertEqual(service.states["gpu"].next_attempt_monotonic, float("inf"))

    def test_repeated_failure_warnings_are_rate_limited(self):
        service = self.service()
        service.logger = Mock()
        failure = ConnectorFailure("proxy_failed", "offline", retryable=True)
        service._record_failure("gpu", failure)
        service._record_failure("gpu", failure)
        self.assertEqual(service.logger.warning.call_count, 1)
        service._failure_log_at[("gpu", "proxy_failed")] = time.monotonic() - 601.0
        service._record_failure("gpu", failure)
        self.assertEqual(service.logger.warning.call_count, 2)


if __name__ == "__main__":
    unittest.main()
