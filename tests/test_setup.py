"""Unit tests for scripts/setup.py."""

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

# Patch setuptools.setup before importing setup module
sys.modules["setuptools"] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import setup  # noqa: E402


class TestReadProjectName(unittest.TestCase):
    """Tests for read_project_name function."""

    def test_read_project_name_success(self) -> None:
        """Test successfully reading project name from pyproject.toml."""
        with patch.object(setup, "PYPROJECT_PATH") as mock_path:
            mock_path.exists.return_value = True
            content = 'name = "test-project"\n'
            with patch("builtins.open", create=True) as mock_file:
                mock_file.return_value.__enter__.return_value = iter([content])
                with patch.object(setup, "PYPROJECT_PATH") as mock_path_2:
                    mock_path_2.exists.return_value = True
                    result = setup.read_project_name()
        self.assertIn("test-project", result)

    def test_read_project_name_file_not_found(self) -> None:
        """Test reading project name when pyproject.toml does not exist."""
        with patch.object(setup, "PYPROJECT_PATH") as mock_path:
            mock_path.exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                setup.read_project_name()

    def test_read_project_name_not_found_in_file(self) -> None:
        """Test reading project name when name field is missing."""
        with patch.object(setup, "PYPROJECT_PATH") as mock_path:
            mock_path.exists.return_value = True
            with patch("builtins.open", create=True) as mock_file:
                mock_file.return_value.__enter__.return_value = iter(
                    ["[tool.poetry]\n", "version = '0.1.0'\n"]
                )
                with self.assertRaises(ValueError):
                    setup.read_project_name()


class TestReadInternalCacheVersion(unittest.TestCase):
    """Tests for read_internal_cache_version function."""

    def test_read_cache_version_exists(self) -> None:
        """Test reading existing cache version."""
        with patch.object(setup, "CACHE_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "1.2.3"
            result = setup.read_internal_cache_version()
        self.assertEqual(result, "1.2.3")

    def test_read_cache_version_not_exists(self) -> None:
        """Test reading cache when file does not exist."""
        with patch.object(setup, "CACHE_PATH") as mock_path:
            mock_path.exists.return_value = False
            result = setup.read_internal_cache_version()
        self.assertIsNone(result)

    def test_read_cache_version_empty(self) -> None:
        """Test reading cache when file is empty."""
        with patch.object(setup, "CACHE_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = ""
            result = setup.read_internal_cache_version()
        self.assertIsNone(result)


class TestFetchLatestVersion(unittest.TestCase):
    """Tests for fetch_latest_version function."""

    def test_fetch_latest_version_success(self) -> None:
        """Test successfully fetching version from PyPI."""
        response_data: dict[str, dict[str, str]] = {"info": {"version": "2.0.0"}}
        with patch.object(setup, "urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = setup.fetch_latest_version("test-project")
        self.assertEqual(result, "2.0.0")

    def test_fetch_latest_version_skip_pypi(self) -> None:
        """Test fetch_latest_version when ENV_SKIP_PYPI is set."""
        with patch.dict(os.environ, {"PYFORGE_SKIP_PYPI": "1"}):
            # Need to reload the module to pick up new env var
            with patch.object(setup, "ENV_SKIP_PYPI", True):
                result = setup.fetch_latest_version("test-project")
        self.assertIsNone(result)

    def test_fetch_latest_version_timeout(self) -> None:
        """Test fetch_latest_version handles timeout."""
        with patch.object(setup, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()
            result = setup.fetch_latest_version("test-project")
        self.assertIsNone(result)

    def test_fetch_latest_version_http_error(self) -> None:
        """Test fetch_latest_version handles HTTP errors."""
        with patch.object(setup, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")
            result = setup.fetch_latest_version("test-project")
        self.assertIsNone(result)

    def test_fetch_latest_version_invalid_response(self) -> None:
        """Test fetch_latest_version with invalid JSON response."""
        with patch.object(setup, "urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = b"invalid json"
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = setup.fetch_latest_version("test-project")
        self.assertIsNone(result)

    def test_fetch_latest_version_invalid_url_scheme(self) -> None:
        """Test fetch_latest_version rejects non-HTTPS URLs."""
        result = setup.fetch_latest_version("http://test-project")
        self.assertIsNone(result)

    def test_fetch_latest_version_missing_version_in_response(self) -> None:
        """Test fetch_latest_version when version key is missing."""
        response_data: dict[str, dict[str, Any]] = {"info": {}}
        with patch.object(setup, "urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.read.return_value = json.dumps(response_data).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = setup.fetch_latest_version("test-project")
        self.assertIsNone(result)


class TestWriteVersionCache(unittest.TestCase):
    """Tests for write_version_cache function."""

    def test_write_version_cache(self) -> None:
        """Test writing version to cache."""
        with patch.object(setup, "CACHE_PATH") as mock_path:
            setup.write_version_cache("1.5.0")
            mock_path.write_text.assert_called_once_with("1.5.0", encoding="utf-8")


class TestUpdateAboutPy(unittest.TestCase):
    """Tests for update_about_py function."""

    def test_update_about_py(self) -> None:
        """Test updating __about__.py file."""
        with patch.object(setup, "ABOUT_PATH") as mock_path:
            mock_parent = Mock()
            mock_path.parent = mock_parent
            setup.update_about_py("2.1.0")
            mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
            mock_path.write_text.assert_called_once_with(
                '__version__ = "2.1.0"\n', encoding="utf-8"
            )

    def test_update_about_py_handles_exception(self) -> None:
        """Test update_about_py handles exceptions gracefully."""
        with patch.object(setup, "ABOUT_PATH") as mock_path:
            mock_path.parent.mkdir.side_effect = Exception("Permission denied")
            with patch("builtins.print") as mock_print:
                setup.update_about_py("2.1.0")
                mock_print.assert_called_once()
                assert "Warning: Failed to update __about__.py" in str(
                    mock_print.call_args
                )


class TestCalculateNextVersion(unittest.TestCase):
    """Tests for calculate_next_version function."""

    def test_calculate_next_version_semantic(self) -> None:
        """Test calculating next version with semantic versioning."""
        result = setup.calculate_next_version("1.2.3")
        self.assertEqual(result, "1.2.4")

    def test_calculate_next_version_two_parts(self) -> None:
        """Test calculating next version with two-part version."""
        result = setup.calculate_next_version("1.5")
        self.assertEqual(result, "1.6")

    def test_calculate_next_version_single_part(self) -> None:
        """Test calculating next version with single-part version."""
        result = setup.calculate_next_version("5")
        self.assertEqual(result, "6")

    def test_calculate_next_version_no_numbers(self) -> None:
        """Test calculating next version when no numbers found."""
        result = setup.calculate_next_version("alpha")
        self.assertEqual(result, "0.0.1")

    def test_calculate_next_version_fallback_without_packaging(self) -> None:
        """Test version calculation fallback when packaging is unavailable."""
        with patch.object(setup, "HAS_PACKAGING", False):
            result = setup.calculate_next_version("1.2.3")
            self.assertEqual(result, "1.2.4")


class TestDynamicVersion(unittest.TestCase):
    """Tests for dynamic_version function."""

    def setUp(self) -> None:
        """Reset global state before each test."""
        setup._computed_version = None

    def test_dynamic_version_from_pypi(self) -> None:
        """Test dynamic_version uses PyPI version."""
        with patch.object(setup, "read_project_name", return_value="test-proj"):
            with patch.object(setup, "fetch_latest_version", return_value="3.0.0"):
                with patch.object(
                    setup, "read_internal_cache_version", return_value=None
                ):
                    result = setup.dynamic_version()
        self.assertEqual(result, "3.0.0")

    def test_dynamic_version_from_cache(self) -> None:
        """Test dynamic_version uses cached version when PyPI unavailable."""
        with patch.object(setup, "read_project_name", return_value="test-proj"):
            with patch.object(setup, "fetch_latest_version", return_value=None):
                with patch.object(
                    setup, "read_internal_cache_version", return_value="2.5.0"
                ):
                    result = setup.dynamic_version()
        self.assertEqual(result, "2.5.0")

    def test_dynamic_version_both_available_pypi_newer(self) -> None:
        """Test dynamic_version prefers newer PyPI version."""
        with patch.object(setup, "read_project_name", return_value="test-proj"):
            with patch.object(setup, "fetch_latest_version", return_value="4.0.0"):
                with patch.object(
                    setup, "read_internal_cache_version", return_value="3.0.0"
                ):
                    result = setup.dynamic_version()
        self.assertEqual(result, "4.0.0")

    def test_dynamic_version_initial_publish(self) -> None:
        """Test dynamic_version on initial publish."""
        with patch.object(setup, "read_project_name", return_value="test-proj"):
            with patch.object(setup, "fetch_latest_version", return_value=None):
                with patch.object(
                    setup, "read_internal_cache_version", return_value=None
                ):
                    with patch.object(setup, "IS_INITIAL_PUBLISH", True):
                        with patch.dict(os.environ, {"PYFORGE_AUTO_INCREMENT": "1"}):
                            with patch.object(setup, "ENV_ALLOW_AUTO_INCREMENT", True):
                                with patch.object(setup, "write_version_cache"):
                                    with patch.object(setup, "update_about_py"):
                                        result = setup.dynamic_version()
                                        self.assertEqual(result, "0.0.1")

    def test_dynamic_version_no_project_name(self) -> None:
        """Test dynamic_version when project name cannot be read."""
        with patch.object(
            setup, "read_project_name", side_effect=Exception("test error")
        ):
            result = setup.dynamic_version()
        self.assertEqual(result, "0.0.0")

    def test_dynamic_version_caching(self) -> None:
        """Test dynamic_version caches result."""
        with patch.object(setup, "read_project_name", return_value="test-proj"):
            with patch.object(setup, "fetch_latest_version", return_value="1.0.0"):
                with patch.object(
                    setup, "read_internal_cache_version", return_value=None
                ):
                    result1 = setup.dynamic_version()
                    result2 = setup.dynamic_version()

        self.assertEqual(result1, result2)
        self.assertEqual(result1, "1.0.0")

    def test_dynamic_version_fallback_without_packaging(self) -> None:
        """Test dynamic_version when packaging is unavailable."""
        with patch.object(setup, "HAS_PACKAGING", False):
            with patch.object(setup, "read_project_name", return_value="test-proj"):
                with patch.object(setup, "fetch_latest_version", return_value="2.0.0"):
                    with patch.object(
                        setup, "read_internal_cache_version", return_value="1.0.0"
                    ):
                        result = setup.dynamic_version()
        self.assertEqual(result, "2.0.0")


if __name__ == "__main__":
    unittest.main()
