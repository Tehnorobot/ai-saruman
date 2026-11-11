"""Recording controls retain edits and do not depend on a selected project."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids[attrs['id']] = (tag, attrs)


class RecordingSettingsUITests(unittest.TestCase):
    def test_recording_labels_are_readable_utf8_text(self):
        html = (ROOT / 'app/index.html').read_text(encoding='utf-8')
        section = html[html.index('id="recordingSettings"'):html.index('id="modelsSlot"')]
        self.assertIn('Запись видео', section)
        self.assertIn('Максимальная длина записи, секунд', section)
        self.assertIn('Сохранить', section)
        self.assertNotRegex(section, r'\?{3,}|\ufffd')

    def test_duration_control_is_accessible_and_has_no_arbitrary_html_ceiling(self):
        html = (ROOT / 'app/index.html').read_text(encoding='utf-8')
        elements = Elements()
        elements.feed(html)
        tag, attrs = elements.ids['recordingMaxSeconds']
        self.assertEqual(tag, 'input')
        self.assertEqual((attrs['type'], attrs['min'], attrs['step']), ('number', '1', '1'))
        self.assertNotIn('max', attrs)
        self.assertIn('recordingSettingsHelp', attrs['aria-describedby'])
        self.assertEqual(elements.ids['recordingSettingsStatus'][1]['role'], 'status')
        self.assertIn('recordingSettingsOpen', elements.ids)
        self.assertLess(html.index('id="recordingSettings"'), html.index('id="modelsSlot"'))
        self.assertIn('/recording-settings.js', html)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for client state checks')
    def test_client_global_state_and_error_recovery(self):
        result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/recording_settings_ui.cjs'),
                                 str(ROOT / 'app/recording-settings.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Recording settings UI: PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
