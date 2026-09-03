"""Исключения механизма Module Bridge."""


class BridgeError(Exception):
    """Базовое исключение для всех ошибок ModuleBridge."""


class DuplicateProvider(BridgeError):
    """
    Попытка зарегистрировать второго провайдера для уже занятого имени операции.

    Чтобы заменить регистрацию — вызывайте provide(..., override=True)
    или предварительно bridge.unregister(name).
    """


class BridgeContractError(BridgeError):
    """Дескриптор platform-контракта не соответствует схеме (BRIDGE_CONTRACTS=raise)."""


class BridgeUnavailable(BridgeError):
    """Peer не ответил или транспорт недоступен. Это не «провайдера нет»."""


class BridgePayloadError(BridgeError):
    """Аргумент нельзя сериализовать в JSON для HTTP-моста или Redis EventBus."""
