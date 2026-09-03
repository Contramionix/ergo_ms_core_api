from rest_framework.exceptions import ValidationError

from django.test import SimpleTestCase

from src.core.utils.media_upload_validation import normalize_target_dir


class NormalizeTargetDirTests(SimpleTestCase):
    def test_ordinary_prefix_allowed(self):
        self.assertEqual(normalize_target_dir('avatars/users'), 'avatars/users')

    def test_knowledge_root_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            normalize_target_dir('knowledge')
        self.assertIn('knowledge', str(ctx.exception.detail))

    def test_knowledge_nested_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_target_dir('knowledge/core/draft')
