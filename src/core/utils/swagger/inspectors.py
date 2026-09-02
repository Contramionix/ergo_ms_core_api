from drf_spectacular.openapi import AutoSchema


def _module_ref_prefix(module: str) -> str:
    parts = module.split('.')
    prefix_parts = []

    if parts and parts[0] == 'src':
        prefix_parts = [p for p in parts[1:-1] if p not in ('api', 'serializers')]
    elif 'modules' in parts:
        idx = parts.index('modules')
        prefix_parts = [p for p in parts[idx + 1:-1] if p not in ('api', 'serializers')]
    elif len(parts) > 1:
        prefix_parts = [parts[-2]]

    return ''.join(
        ''.join(word.capitalize() for word in part.split('_'))
        for part in prefix_parts
    )


class UniqueRefNameAutoSchema(AutoSchema):
    """Уникальные имена компонентов схемы для одноимённых сериализаторов в разных модулях."""

    def get_component_name(self, serializer, *args, **kwargs):
        meta = getattr(serializer, 'Meta', None)
        if meta is not None and hasattr(meta, 'ref_name'):
            return meta.ref_name

        class_name = serializer.__class__.__name__
        if class_name.endswith('Serializer'):
            base_name = class_name[:-len('Serializer')]
        elif class_name.endswith('SerializerMixin'):
            base_name = class_name[:-len('SerializerMixin')]
        else:
            base_name = class_name

        prefix = _module_ref_prefix(serializer.__class__.__module__)
        return f'{prefix}{base_name}' if prefix else base_name
