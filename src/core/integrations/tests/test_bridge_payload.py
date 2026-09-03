from types import SimpleNamespace

from django.test import SimpleTestCase

from src.core.integrations.transports.payload import (
    jsonable_menu_item,
    jsonable_notification,
    menu_item_from_payload,
    prepare_incoming_kwargs,
    prepare_outgoing_kwargs,
)


class BridgePayloadTests(SimpleTestCase):
    def test_menu_item_roundtrip(self):
        parent = SimpleNamespace(name='tech', route_name='tech.folder')
        item = SimpleNamespace(
            route_name='mod.page',
            name='page',
            module_source='mod',
            parent=parent,
        )
        restored = menu_item_from_payload(jsonable_menu_item(item))
        self.assertEqual(restored.route_name, 'mod.page')
        self.assertEqual(restored.name, 'page')
        self.assertEqual(restored.parent.name, 'tech')
        self.assertEqual(restored.parent.route_name, 'tech.folder')

    def test_outgoing_keeps_ids_and_flat_item(self):
        user = SimpleNamespace(pk=5, public_id='abc', is_authenticated=True)
        item = SimpleNamespace(
            route_name='r',
            name='n',
            module_source='',
            parent=None,
        )
        outgoing = prepare_outgoing_kwargs({'user': user, 'item': item, 'flag': True})
        self.assertEqual(outgoing['user_id'], 5)
        self.assertEqual(outgoing['user_public_id'], 'abc')
        self.assertNotIn('user', outgoing)
        self.assertEqual(outgoing['item']['route_name'], 'r')
        self.assertTrue(outgoing['flag'])
        incoming = prepare_incoming_kwargs(outgoing)
        self.assertEqual(incoming['item'].route_name, 'r')

    def test_notification_fields_without_orm(self):
        recipient = SimpleNamespace(pk=9)
        notification = SimpleNamespace(
            pk=3,
            public_id='nid',
            title='Hello',
            body='Body',
            level='info',
            icon='',
            source_module='mod',
            event_key='evt',
            link_url='',
            route=None,
            meta={},
            recipient=recipient,
        )
        raw = jsonable_notification(notification)
        self.assertEqual(raw['title'], 'Hello')
        self.assertEqual(raw['recipient_id'], 9)
        self.assertEqual(raw['source_module'], 'mod')
