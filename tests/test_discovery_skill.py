"""Built-in discovery skill and translated display metadata use source-only records."""
import json
import unittest
from unittest.mock import patch

from paperwhale_server.services import research_discovery as discovery, skills


class DiscoverySkillTests(unittest.TestCase):
    def run_pipeline(self, translated='真实光学论文', title='Real optics paper'):
        source = {'title':title,'authors':'Actual Author','year':'2025','doi':'10.1234/real',
                  'arxiv_id':'','url':'https://doi.org/10.1234/real','abstract':'Optics abstract',
                  'sources':[{'name':'Crossref','url':'https://doi.org/10.1234/real'}]}
        values = [{'intent':'search','queries':['optics'],'topic':'光学'},
                  {'items':[{'index':1,'title_zh':translated,'reason':'题名相关','stage':''}]}]
        messages = []
        def reply(current, **kwargs):
            messages.append(current)
            return {'content':json.dumps(values[len(messages)-1],ensure_ascii=False)}
        with patch.object(discovery.llm,'chat',side_effect=reply), patch.object(discovery.sources,'collect',return_value=([source],[])):
            result = discovery.research('找光学论文',[], '')
        return result, messages, source

    def test_skill_is_bundled_and_actually_in_both_model_prompts(self):
        result, messages, _ = self.run_pipeline()
        self.assertIn('academic-discovery',[row['id'] for row in skills.list_skills()])
        for current in messages:
            self.assertIn('academic-discovery',current[0]['content'])
            self.assertIn('同义词',current[0]['content'])
            self.assertIn('元数据',current[0]['content'])

    def test_chinese_title_is_additive_and_never_replaces_original_metadata(self):
        result, _, source = self.run_pipeline()
        row = result['items'][0]
        self.assertEqual(row['title_zh'],'真实光学论文')
        for key in ('title','authors','doi','url','abstract','sources'):
            self.assertEqual(row[key],source[key])

    def test_missing_or_invalid_translation_remains_retryable(self):
        for title_zh in ('','Untranslated English','https://bad.invalid [4]'):
            with self.subTest(title_zh=title_zh):
                result, _, _ = self.run_pipeline(title_zh)
                self.assertEqual(result['items'][0]['title_zh'],'')
                self.assertTrue(any('中文题名' in warning for warning in result['warnings']))

    def test_native_chinese_title_does_not_require_a_second_translation(self):
        result, _, _ = self.run_pipeline('', '真实光学论文')
        self.assertEqual(result['items'][0]['title_zh'],'真实光学论文')


if __name__ == '__main__':
    unittest.main()
