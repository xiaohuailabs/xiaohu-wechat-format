import contextlib
import importlib.util
import json
import shutil
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_PATH = REPO_ROOT / "scripts" / "format.py"
PUBLISH_PATH = REPO_ROOT / "scripts" / "publish.py"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sample_article.md"
TEST_TMP_ROOT = REPO_ROOT / ".tmp_test_runtime"


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def local_temp_dir():
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmpdir = TEST_TMP_ROOT / uuid.uuid4().hex
    tmpdir.mkdir(parents=True, exist_ok=False)
    try:
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


class FormatSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.format_mod = load_module("wechat_format_test", FORMAT_PATH)
        cls.publish_mod = load_module("wechat_publish_test", PUBLISH_PATH)
        cls.fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_non_minimal_theme_rejects_variant_args(self):
        with self.assertRaises(ValueError):
            self.format_mod.build_style_selection("newspaper", accent="blue")
        with self.assertRaises(ValueError):
            self.format_mod.build_style_selection("newspaper", strong_style="highlight")

    def test_default_output_dir_is_article_sibling_wechat_output(self):
        output_dir = self.format_mod.resolve_output_dir(FIXTURE_PATH, None)
        self.assertEqual(output_dir, FIXTURE_PATH.parent / "wechat output")

    def test_minimal_variant_theme_application(self):
        base_theme = self.format_mod.load_theme(self.format_mod.MINIMAL_FLEX_THEME_ID)
        selection = self.format_mod.build_style_selection(
            self.format_mod.MINIMAL_FLEX_THEME_ID,
            accent="blue",
            heading_align="center",
            divider_style="fade-short",
            strong_style="highlight",
        )
        final_theme = self.format_mod.apply_theme_variants(
            self.format_mod.MINIMAL_FLEX_THEME_ID,
            base_theme,
            selection,
        )

        self.assertEqual(
            final_theme["styles"]["h1"]["color"],
            self.format_mod.MINIMAL_FLEX_ACCENTS["blue"]["hex"],
        )
        self.assertEqual(final_theme["styles"]["h5"]["text_align"], "center")
        self.assertEqual(final_theme["styles"]["h2"]["text_align"], "center")
        self.assertEqual(final_theme["styles"]["hr"]["width"], "50%")
        self.assertIn("linear-gradient", final_theme["styles"]["hr"]["background"])
        self.assertIn("linear-gradient", final_theme["styles"]["strong"]["background"])

    def test_format_output_contains_variant_styles(self):
        base_theme = self.format_mod.load_theme(self.format_mod.MINIMAL_FLEX_THEME_ID)
        selection = self.format_mod.build_style_selection(
            self.format_mod.MINIMAL_FLEX_THEME_ID,
            accent="green",
            heading_align="center",
            divider_style="bold-short",
            strong_style="highlight",
        )
        final_theme = self.format_mod.apply_theme_variants(
            self.format_mod.MINIMAL_FLEX_THEME_ID,
            base_theme,
            selection,
        )

        with local_temp_dir() as tmpdir:
            result = self.format_mod.format_for_output(
                self.fixture_text,
                FIXTURE_PATH,
                final_theme,
                tmpdir,
                REPO_ROOT,
                "wechat",
            )

        self.assertIn(self.format_mod.MINIMAL_FLEX_ACCENTS["green"]["hex"], result["html"])
        self.assertIn("text-align:center", result["html"])
        self.assertIn("width:60%", result["html"])
        self.assertIn("height:3px", result["html"])
        self.assertIn("linear-gradient(180deg", result["html"])

    def test_selection_files_keep_backward_compatibility(self):
        selection = self.format_mod.build_style_selection(
            self.format_mod.MINIMAL_FLEX_THEME_ID,
            accent="red",
            heading_align="right",
            divider_style="none",
            strong_style="highlight",
            font_size=16,
        )

        with local_temp_dir() as out_dir:
            self.format_mod.write_selected_style(selection, out_dir)
            style_path = out_dir / "selected-style.json"
            theme_path = out_dir / "selected-theme.txt"

            self.assertTrue(style_path.exists())
            self.assertTrue(theme_path.exists())

            payload = json.loads(style_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["theme_id"], self.format_mod.MINIMAL_FLEX_THEME_ID)
            self.assertEqual(payload["accent"], "red")
            self.assertEqual(payload["strong_style"], "highlight")
            self.assertEqual(theme_path.read_text(encoding="utf-8").strip(), self.format_mod.MINIMAL_FLEX_THEME_ID)

    def test_publish_can_read_structured_selection(self):
        payload = {
            "theme_id": self.format_mod.MINIMAL_FLEX_THEME_ID,
            "accent": "gray",
            "heading_align": "left",
            "divider_style": "solid-full",
            "strong_style": "color",
            "font_size": 15,
        }

        with local_temp_dir() as tmpdir:
            path = tmpdir / "selected-style.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            loaded = self.publish_mod.load_selected_style(path)

        self.assertEqual(loaded["theme_id"], self.format_mod.MINIMAL_FLEX_THEME_ID)
        self.assertEqual(loaded["accent"], "gray")
        self.assertEqual(loaded["strong_style"], "color")

    def test_templates_are_assistant_neutral(self):
        gallery_html = (REPO_ROOT / "templates" / "gallery.html").read_text(encoding="utf-8")
        preview_html = (REPO_ROOT / "templates" / "preview.html").read_text(encoding="utf-8")
        self.assertNotIn("Claude", gallery_html)
        self.assertNotIn("Claude", preview_html)


if __name__ == "__main__":
    unittest.main()
