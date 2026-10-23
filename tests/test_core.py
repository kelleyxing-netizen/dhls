"""Publishing checks and failure cases for portable local links and config."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dhls.core.validate import check_repository, local_link_errors
from dhls.utils.paths import artifact_paths


class PublishingTests(unittest.TestCase):
    def temporary_directory(self):
        # Keep fixtures inside the workspace, including restricted desktop runs.
        parent = Path(__file__).resolve().parents[1] / ".research/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=parent)

    def test_deliverables_are_complete_and_portable(self):
        self.assertEqual([], check_repository())

    def test_links_handle_cjk_spaces_and_anchors(self):
        with self.temporary_directory() as tmp:
            root = Path(tmp)
            (root / "说明 文件.md").write_text("# 标题", encoding="utf-8")
            md = root / "README.md"
            md.write_text("[说明](说明%20文件.md#标题)\n[外部](https://example.com)", encoding="utf-8")
            self.assertEqual([], local_link_errors(md, root))

    def test_missing_image_is_rejected(self):
        with self.temporary_directory() as tmp:
            root = Path(tmp)
            md = root / "README.md"
            md.write_text("![图](missing.png)", encoding="utf-8")
            self.assertIn("Broken local link", local_link_errors(md, root)[0])

    def test_parent_link_cannot_escape_repository(self):
        with self.temporary_directory() as tmp:
            root = Path(tmp)
            md = root / "README.md"
            md.write_text("[outside](../private.md)", encoding="utf-8")
            self.assertIn("escapes repository", local_link_errors(md, root)[0])

    def test_config_cannot_escape_repository(self):
        with self.temporary_directory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/default.json").write_text(json.dumps({"source": "../private.md"}))
            with self.assertRaises(ValueError):
                artifact_paths(root)


if __name__ == "__main__":
    unittest.main()
