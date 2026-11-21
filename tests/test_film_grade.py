"""Tests for vstudio_film_grade.py. Filter-string building only (no ffmpeg).

Run: python3 -m unittest tests.test_film_grade -v
"""
import io
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vstudio_film_grade as FG


# Frozen strings from the original 1920x1080, 24 fps film_grade.py (exact match required).
MEDIUM = (
    "[0:v]fps=24,scale=1932:1092:flags=lanczos,setsar=1,"
    "crop=1920:1080:x='6+1.1*sin(t*2.3)+0.55*sin(t*7.7+1.3)':"
    "y='6+0.88*sin(t*1.7+0.5)+0.44*sin(t*9.1)',"
    "gblur=sigma=0.55,format=gbrp,"
    "curves=all='0/0.04 0.25/0.244 0.5/0.49 0.75/0.730 0.9/0.850 1/0.92',"
    "colorbalance=rs=0.015:gs=0.004:bs=-0.030:rm=0.025:gm=0.005:bm=-0.025:rh=0.020:gh=0.008:bh=-0.030,"
    "eq=saturation=0.82:contrast=0.98:gamma=1.07,format=gbrp,split[b0][b1];"
    "[b1]curves=all='0/0 0.72/0 1/1',gblur=sigma=22,"
    "colorchannelmixer=rr=1:gg=0.38:bb=0.12[h];"
    "[b0][h]blend=all_mode=screen:all_opacity=0.35[hb];"
    "color=c=0x808080:s=1920x1080:r=24,format=gbrp,noise=alls=60:allf=t,"
    "gblur=sigma=0.9,eq=saturation=0.15[g];"
    "[hb][g]blend=all_mode=overlay:all_opacity=0.16:shortest=1,"
    "vignette=a=PI/7.5,format=yuv420p,crop=1920:804:0:138,pad=1920:1080:0:138:black[v]"
)
LIGHT = (
    "[0:v]fps=24,scale=1930:1090:flags=lanczos,setsar=1,"
    "crop=1920:1080:x='5+0.6*sin(t*2.3)+0.30*sin(t*7.7+1.3)':"
    "y='5+0.48*sin(t*1.7+0.5)+0.24*sin(t*9.1)',"
    "gblur=sigma=0.35,format=gbrp,"
    "curves=all='0/0.025 0.25/0.235 0.5/0.49 0.75/0.734 0.9/0.854 1/0.95',"
    "colorbalance=rs=0.009:gs=0.002:bs=-0.018:rm=0.015:gm=0.003:bm=-0.015:rh=0.012:gh=0.005:bh=-0.018,"
    "eq=saturation=0.9:contrast=0.98:gamma=1.05,format=gbrp,split[b0][b1];"
    "[b1]curves=all='0/0 0.72/0 1/1',gblur=sigma=22,"
    "colorchannelmixer=rr=1:gg=0.38:bb=0.12[h];"
    "[b0][h]blend=all_mode=screen:all_opacity=0.22[hb];"
    "color=c=0x808080:s=1920x1080:r=24,format=gbrp,noise=alls=60:allf=t,"
    "gblur=sigma=0.9,eq=saturation=0.15[g];"
    "[hb][g]blend=all_mode=overlay:all_opacity=0.1:shortest=1,"
    "vignette=a=PI/9,format=yuv420p,crop=1920:804:0:138,pad=1920:1080:0:138:black[v]"
)
STRONG = (
    "[0:v]fps=24,scale=1936:1096:flags=lanczos,setsar=1,"
    "crop=1920:1080:x='8+1.8*sin(t*2.3)+0.90*sin(t*7.7+1.3)':"
    "y='8+1.44*sin(t*1.7+0.5)+0.72*sin(t*9.1)',"
    "gblur=sigma=0.85,format=gbrp,"
    "curves=all='0/0.06 0.25/0.256 0.5/0.49 0.75/0.726 0.9/0.846 1/0.89',"
    "colorbalance=rs=0.021:gs=0.006:bs=-0.042:rm=0.035:gm=0.007:bm=-0.035:rh=0.028:gh=0.011:bh=-0.042,"
    "eq=saturation=0.74:contrast=0.98:gamma=1.09,format=gbrp,split[b0][b1];"
    "[b1]curves=all='0/0 0.72/0 1/1',gblur=sigma=22,"
    "colorchannelmixer=rr=1:gg=0.38:bb=0.12[h];"
    "[b0][h]blend=all_mode=screen:all_opacity=0.5[hb];"
    "color=c=0x808080:s=1920x1080:r=24,format=gbrp,noise=alls=60:allf=t,"
    "gblur=sigma=0.9,eq=saturation=0.15[g];"
    "[hb][g]blend=all_mode=overlay:all_opacity=0.24:shortest=1,"
    "vignette=a=PI/6,format=yuv420p,crop=1920:804:0:138,pad=1920:1080:0:138:black[v]"
)
MEDIUM_NO_LB = MEDIUM.replace(
    ",crop=1920:804:0:138,pad=1920:1080:0:138:black[v]", "[v]"
)
MEDIUM_YOFF20 = MEDIUM.replace("crop=1920:804:0:138,pad=", "crop=1920:804:0:20,pad=")


def _stdout(argv):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = FG.main(argv)
    return rc, buf.getvalue(), err.getvalue()


class FilterStrings(unittest.TestCase):
    def test_medium_1920x1080_matches_original(self):
        self.assertEqual(FG.build("medium"), MEDIUM)
        self.assertEqual(FG.build("medium", True, None, (1920, 1080), 24), MEDIUM)

    def test_light_and_strong_match_original(self):
        self.assertEqual(FG.build("light"), LIGHT)
        self.assertEqual(FG.build("strong"), STRONG)

    def test_no_letterbox_and_yoff(self):
        self.assertEqual(FG.build("medium", letterbox=False), MEDIUM_NO_LB)
        self.assertEqual(FG.build("medium", True, 20), MEDIUM_YOFF20)
        self.assertNotIn("pad=1920:1080", FG.build("medium", letterbox=False))

    def test_fps_stays_unpadded_integer(self):
        self.assertIn("fps=24,", MEDIUM)
        self.assertIn("r=24,", MEDIUM)
        self.assertNotIn("fps=24.0", FG.build("medium", fps=24.0))
        s = FG.build("medium", letterbox=False, fps=30)
        self.assertIn("fps=30,", s)
        self.assertIn("r=30,", s)

    def test_unknown_preset(self):
        with self.assertRaises(FG.GradeError):
            FG.build("film")


class Letterbox(unittest.TestCase):
    def test_geometry_1920x1080(self):
        ah, bar, y = FG.letterbox_geometry(1920, 1080)
        self.assertEqual((ah, bar, y), (804, 138, 138))
        self.assertEqual(FG.letterbox_geometry(1920, 1080, 20)[2], 20)
        self.assertEqual(FG.letterbox_geometry(1920, 1080, 9999)[2], 276)
        self.assertEqual(FG.letterbox_geometry(1920, 1080, -5)[2], 0)

    def test_default_on_for_landscape_off_for_vertical(self):
        self.assertTrue(FG.letterbox_default(1920, 1080))
        self.assertFalse(FG.letterbox_default(1080, 1920))
        self.assertFalse(FG.letterbox_default(1080, 1080))
        self.assertTrue(FG.resolve_letterbox(None, 1920, 1080))
        self.assertFalse(FG.resolve_letterbox(None, 1080, 1920))
        self.assertTrue(FG.resolve_letterbox(True, 1080, 1920))
        self.assertFalse(FG.resolve_letterbox(False, 1920, 1080))

    def test_vertical_filter_omits_letterbox_unless_forced(self):
        off = FG.build("medium", FG.resolve_letterbox(None, 1080, 1920), None, (1080, 1920), 24)
        self.assertIn("s=1080x1920:r=24", off)
        self.assertNotIn("pad=1080:1920", off)
        on = FG.build("medium", True, None, (1080, 1920), 24)
        self.assertIn("crop=1080:452:0:734,pad=1080:1920:0:734:black[v]", on)

    def test_parse_size(self):
        self.assertEqual(FG.parse_size("1920x1080"), (1920, 1080))
        self.assertEqual(FG.parse_size("1080X1920"), (1080, 1920))
        for bad in ("foo", "0x1080", "1921x1080", "-2x2", "1920"):
            with self.assertRaises(FG.GradeError):
                FG.parse_size(bad)


class CLI(unittest.TestCase):
    def test_bare_preset_prints_filter(self):
        rc, out, err = _stdout(["medium"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), MEDIUM)

    def test_filter_subcommand_and_flags(self):
        rc, out, err = _stdout(["filter", "medium", "--no-letterbox"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), MEDIUM_NO_LB)
        rc, out, err = _stdout(["medium", "--yoff=20"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), MEDIUM_YOFF20)

    def test_vertical_size_defaults_to_no_letterbox(self):
        rc, out, err = _stdout(["medium", "--size", "1080x1920"])
        self.assertEqual(rc, 0)
        self.assertNotIn("pad=1080:1920", out)
        rc, out, err = _stdout(["medium", "--size", "1080x1920", "--letterbox"])
        self.assertEqual(rc, 0)
        self.assertIn("pad=1080:1920:0:734:black", out)

    def test_bad_size_exits_1(self):
        rc, out, err = _stdout(["medium", "--size", "odd"])
        self.assertEqual(rc, 1)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
