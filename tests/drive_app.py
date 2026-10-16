"""Drive the app offscreen and save screenshots (development only; not part of the app)."""
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
app = QApplication(sys.argv[:1])
# keep the app's real settings (language, appearance, the first-open answer) out of the tests
import tempfile  # noqa: E402
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
QCoreApplication.setOrganizationName('GR4-LUTSmith-tests')
QCoreApplication.setApplicationName('GR4-LUTSmith-tests')
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp(prefix='gr4-lutsmith-tests-'))
from lutsmith_app import main as M, theme  # noqa: E402

theme.load_fonts()


def shot(w, name):
    app.processEvents()
    w.grab().save(str(OUT / f'{name}.png'))
    print('saved', name)
