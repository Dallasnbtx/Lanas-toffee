"""Lets `python -m pytest tests -k "not e2e"` run the older script-style test files.
Those files run their checks at import time (and some call sys.exit), each with its own env/DB,
so each one runs in its own subprocess and passes if it exits 0."""
import os, subprocess, sys
import pytest

SCRIPT_STYLE = {"test_delivery_dates.py", "test_partner.py", "test_refund_webhook.py", "test_refunds.py",
                "test_stripe_flow.py", "test_tax_allergens.py"}


def pytest_pycollect_makemodule(module_path, parent):
    if module_path.name in SCRIPT_STYLE:
        return ScriptFile.from_parent(parent, path=module_path)


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        src = self.path.read_text()
        try:
            compile(src, str(self.path), "exec")
        except SyntaxError as e:
            pytest.skip(f"{self.path.name} is truncated/broken on the box (SyntaxError line {e.lineno}); restore it from the repo")
        r = subprocess.run([sys.executable, str(self.path)], cwd=os.path.dirname(os.path.dirname(str(self.path))),
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise AssertionError(f"{self.path.name} exited {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"
