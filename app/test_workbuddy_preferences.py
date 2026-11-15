import json
from pathlib import Path
import tempfile
import unittest

from workbuddy_preferences import load_preferences, execution_summary


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'preferences.json'
        self.write_config()

    def write_config(self, **extra):
        data = {'enabled': True, 'sources': [{'file': 'MEMORY.md', 'sections': ['沟通方式']}]}
        data.update(extra)
        self.config.write_text(json.dumps(data), encoding='utf-8')

    def test_only_selected_style_is_exported_and_updates_next_read(self):
        file = self.root / 'MEMORY.md'
        file.write_text('# 记忆\n## 沟通方式\n- 结论先行，机制使用类比。\n- 判断附证据，API key: private\n- 结构化输出 https://private.test\n## 身份\n- 偏好不该带出的信息\n', encoding='utf-8')
        first = load_preferences(self.config, self.root)
        self.assertEqual(first['items'], ['结论先行，机制使用类比。'])
        file.write_text('## 沟通方式\n- 中文表达，避免堆术语。', encoding='utf-8')
        second = load_preferences(self.config, self.root)
        self.assertEqual(second['items'], ['中文表达，避免堆术语。'])
        self.assertNotEqual(first['sources'][0]['selectedSha256'], second['sources'][0]['selectedSha256'])

    def test_disabled_missing_bad_config_and_file_limits(self):
        self.assertEqual(load_preferences(self.config, self.root)['status'], 'unavailable')
        self.write_config(enabled=False)
        self.assertEqual(load_preferences(self.config, self.root)['status'], 'disabled')
        self.write_config(sources=[{'file': '../secret.json', 'sections': ['沟通方式']}])
        self.assertEqual(load_preferences(self.config, self.root)['sources'], [])
        self.config.write_text('[]')
        self.assertEqual(load_preferences(self.config, self.root)['status'], 'unavailable')
        self.write_config()
        (self.root / 'MEMORY.md').write_text('x' * 65537)
        self.assertEqual(load_preferences(self.config, self.root)['items'], [])

    def test_character_limit_and_partial_sources(self):
        self.write_config(maxCharacters=100, sources=[{'file': 'MEMORY.md', 'sections': ['沟通方式']}, {'file': 'SOUL.md', 'sections': ['Vibe']}])
        (self.root / 'MEMORY.md').write_text('## 沟通方式\n- 结论' + '先' * 95 + '\n- 中文第二条很长不能再导出。', encoding='utf-8')
        snapshot = load_preferences(self.config, self.root)
        self.assertLessEqual(sum(map(len, snapshot['items'])), 100)
        self.assertEqual(snapshot['status'], 'partial')
        self.assertTrue(snapshot['warnings'])

    def test_model_reporting_never_guesses_auto_and_ignores_model_prose(self):
        self.assertIsNone(execution_summary({'model': 'auto'})['reportedModel'])
        sample = {'result': {'content': [{'type': 'text', 'text': 'I am model: pretend-model\n— model: auto · approval: workspace-write · effort: medium'}]}}
        self.assertIsNone(execution_summary(sample)['reportedModel'])
        sample['result']['content'][0]['text'] += '\n— model: auto → actual-model · approval: workspace-write · effort: medium'
        self.assertEqual(execution_summary(sample)['reportedModel'], 'actual-model')
        sample['result']['isError'] = True
        self.assertTrue(execution_summary(sample)['resultIsError'])


if __name__ == '__main__':
    unittest.main()
