"""Regression tests for transient systemd service configuration."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from memoryguard.ai_load import build_ollama_env


class OllamaEnvironmentTests(unittest.TestCase):
    def test_home_is_set_when_systemd_does_not_supply_it(self):
        with patch.dict(os.environ, {}, clear=True):
            env = build_ollama_env(Path("/var/lib/memoryguard/models"))
        self.assertEqual(env["HOME"], "/root")
        self.assertEqual(env["OLLAMA_MODELS"], "/var/lib/memoryguard/models")
        self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11435")

    def test_bad_or_empty_home_is_replaced_inside_disposable_vm(self):
        with patch.dict(os.environ, {"HOME": ""}, clear=True):
            env = build_ollama_env(Path("/tmp/models"))
        self.assertEqual(env["HOME"], "/root")
        self.assertEqual(env["OLLAMA_MODELS"], "/tmp/models")


if __name__ == "__main__":
    unittest.main()
