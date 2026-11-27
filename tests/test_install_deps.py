"""Installer dependency step: externally managed Python must not look like a failed install."""
import os, re, stat, subprocess, sys, sysconfig, tempfile, textwrap, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"


def _fake_python(log_path):
    return textwrap.dedent(f"""\
        #!/bin/bash
        printf '%s\\n' "$*" >> "{log_path}"
        if [[ "$*" == *EXTERNALLY-MANAGED* ]]; then
          if [[ "${{FAKE_MARKER:-0}}" == 1 ]]; then exit 0; else exit 1; fi
        fi
        if [[ "$*" == *"import numpy"* ]]; then
          if [[ "${{FAKE_DEPS:-0}}" == 1 ]]; then exit 0; else exit 1; fi
        fi
        if [[ "$*" == *pip* ]]; then
          if [[ "$*" == *break-system-packages* ]]; then
            echo PIP_BREAK >> "{log_path}"
            exit 0
          fi
          echo "error: externally-managed-environment" >&2
          echo PIP_PLAIN >> "{log_path}"
          exit 1
        fi
        echo "unexpected python3: $*" >&2
        exit 99
        """)


class InstallDeps(unittest.TestCase):
    def _run(self, marker, deps):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "log"
            bin_dir = d / "bin"
            bin_dir.mkdir()
            exe = bin_dir / "python3"
            exe.write_text(_fake_python(log))
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["FAKE_MARKER"] = "1" if marker else "0"
            env["FAKE_DEPS"] = "1" if deps else "0"
            p = subprocess.run(["bash", str(INSTALL), "--deps-only"], capture_output=True, text=True, env=env)
            return p, log.read_text() if log.exists() else ""

    def test_marker_skips_the_scary_pip_error(self):
        p, log = self._run(marker=True, deps=False)
        self.assertEqual(p.returncode, 0, p.stderr + log)
        self.assertNotIn("externally-managed-environment", p.stdout + p.stderr)
        self.assertEqual(log.count("PIP_PLAIN"), 0)
        self.assertEqual(log.count("PIP_BREAK"), 1)

    def test_ready_imports_do_not_call_pip(self):
        p, log = self._run(marker=True, deps=True)
        self.assertEqual(p.returncode, 0, p.stderr + log)
        self.assertNotIn("PIP_", log)
        self.assertNotIn("externally-managed-environment", p.stdout + p.stderr)

    def test_unmanaged_fallback_still_installs(self):
        p, log = self._run(marker=False, deps=False)
        self.assertEqual(p.returncode, 0, p.stderr + log)
        self.assertIn("PIP_PLAIN", log)
        self.assertIn("PIP_BREAK", log)

    def test_marker_oneliner_matches_this_interpreter(self):
        text = INSTALL.read_text()
        m = re.search(r"python3 -c '([^']*EXTERNALLY-MANAGED[^']*)'", text)
        self.assertIsNotNone(m)
        r = subprocess.run([sys.executable, "-c", m.group(1)])
        marker = Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
        self.assertEqual(r.returncode, 0 if marker.is_file() else 1)


if __name__ == "__main__":
    unittest.main()
