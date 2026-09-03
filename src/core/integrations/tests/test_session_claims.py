from django.test import SimpleTestCase

from src.core.integrations.session_context import (
    coerce_session_claim_value,
    session_claims_key_part,
)


class SessionClaimsHelpersTests(SimpleTestCase):
    def test_coerce_session_claim_value(self):
        self.assertEqual(coerce_session_claim_value(7), 7)
        self.assertEqual(coerce_session_claim_value('12'), 12)
        self.assertIsNone(coerce_session_claim_value(None))
        self.assertIsNone(coerce_session_claim_value(''))
        self.assertIsNone(coerce_session_claim_value('x'))

    def test_session_claims_key_part_stable(self):
        self.assertEqual(session_claims_key_part(None), 's0')
        self.assertEqual(session_claims_key_part({}), 's0')
        left = session_claims_key_part({'b': 2, 'a': 1})
        right = session_claims_key_part({'a': 1, 'b': 2})
        self.assertEqual(left, right)
        self.assertNotEqual(left, session_claims_key_part({'a': 1}))
