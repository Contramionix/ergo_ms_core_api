"""
Менеджер параллелизма задач по очередям для Celery.
Позволяет ограничивать количество одновременных задач для каждой очереди отдельно.
"""

import logging
import threading
import functools
from typing import Dict, Optional, Callable, Any

from django.core.cache import cache

logger = logging.getLogger('celery.concurrency')


class _SlotGate:
    """Счётчик слотов: смену лимита можно делать без сброса уже взятых."""

    def __init__(self, limit: int):
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.limit = max(0, int(limit))
        self.held = 0

    def acquire(self, blocking: bool = False) -> bool:
        with self._cv:
            if self.limit <= 0:
                return False
            if self.held < self.limit:
                self.held += 1
                return True
            if not blocking:
                return False
            while self.held >= self.limit > 0:
                self._cv.wait()
            if self.limit <= 0:
                return False
            self.held += 1
            return True

    def release(self) -> None:
        with self._cv:
            if self.held > 0:
                self.held -= 1
            self._cv.notify_all()

    def set_limit(self, limit: int) -> None:
        with self._cv:
            self.limit = max(0, int(limit))
            self._cv.notify_all()


class QueueConcurrencyManager:
    """
    Менеджер для ограничения количества одновременных задач по очередям.
    
    Поддерживает два режима:
    - threading.Semaphore для single-process worker'а
    - Django cache для distributed worker'ов
    
    Использование:
        manager = QueueConcurrencyManager()
        manager.set_queue_limit('my_module', 4)
        
        # В задаче:
        with manager.acquire('my_module'):
            # Выполнение задачи
    """
    
    _instance: Optional['QueueConcurrencyManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'QueueConcurrencyManager':
        """Singleton паттерн для глобального доступа к менеджеру"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._queue_limits: Dict[str, int] = {}
        self._semaphores: Dict[str, _SlotGate] = {}
        self._semaphore_lock = threading.Lock()
        self._paused_queues: set[str] = set()
        self._queue_classes: Dict[str, str] = {}
        self._non_light_cap = 0
        self._non_light_gate: Optional[_SlotGate] = None
        self._use_distributed = False  # Использовать распределенные блокировки через cache
        self._cache_ttl = 3600  # TTL для cache-based семафоров (1 час)
        self._retry_delay = 60  # Задержка перед повторной попыткой (секунды)
        self._initialized = True
        
        logger.info("QueueConcurrencyManager инициализирован")
    
    def configure(
        self,
        use_distributed: bool = False,
        cache_ttl: int = 3600,
        retry_delay: int = 60
    ):
        """
        Настраивает менеджер параллелизма.
        
        Args:
            use_distributed: Использовать распределенные блокировки через Django cache
            cache_ttl: TTL для cache-based семафоров в секундах
            retry_delay: Задержка перед повторной попыткой в секундах
        """
        self._use_distributed = use_distributed
        self._cache_ttl = cache_ttl
        self._retry_delay = retry_delay
        logger.info(
            f"QueueConcurrencyManager настроен: distributed={use_distributed}, "
            f"cache_ttl={cache_ttl}, retry_delay={retry_delay}"
        )
    
    def set_queue_limit(self, queue_name: str, max_concurrent: int):
        """
        Устанавливает лимит параллельных задач для очереди.
        
        Args:
            queue_name: Имя очереди
            max_concurrent: Максимальное количество одновременных задач (0 = без ограничений)
        """
        if max_concurrent <= 0:
            self._queue_limits.pop(queue_name, None)
            with self._semaphore_lock:
                self._semaphores.pop(queue_name, None)
            logger.debug(f"Очередь {queue_name}: без ограничений параллелизма")
            return
        self.update_queue_limit(queue_name, max_concurrent)

    def update_queue_limit(self, queue_name: str, max_concurrent: int) -> None:
        """Меняет лимит, не сбрасывая уже взятые слоты."""
        if max_concurrent <= 0:
            self.set_queue_limit(queue_name, 0)
            return
        self._queue_limits[queue_name] = max_concurrent
        with self._semaphore_lock:
            gate = self._semaphores.get(queue_name)
            if gate is None:
                self._semaphores[queue_name] = _SlotGate(max_concurrent)
            else:
                gate.set_limit(max_concurrent)
        logger.debug(f"Очередь {queue_name}: max_concurrent_tasks={max_concurrent}")

    def set_paused(self, queue_name: str, paused: bool) -> None:
        if paused:
            self._paused_queues.add(queue_name)
        else:
            self._paused_queues.discard(queue_name)

    def is_paused(self, queue_name: str) -> bool:
        return queue_name in self._paused_queues

    def set_queue_classes(self, classes: Dict[str, str]) -> None:
        self._queue_classes = {
            str(name): str(task_class).strip().lower()
            for name, task_class in classes.items()
            if str(name).strip() and str(task_class).strip()
        }

    def set_non_light_cap(self, cap: int) -> None:
        parsed = max(0, int(cap))
        self._non_light_cap = parsed
        if parsed <= 0:
            self._non_light_gate = None
            return
        gate = self._non_light_gate
        if gate is None:
            self._non_light_gate = _SlotGate(parsed)
        else:
            gate.set_limit(parsed)

    def is_light_queue(self, queue_name: str) -> bool:
        default_class = 'light' if queue_name == 'default' else 'medium'
        return self._queue_classes.get(queue_name, default_class) == 'light'

    def _needs_non_light_slot(self, queue_name: str) -> bool:
        return self._non_light_cap > 0 and not self.is_light_queue(queue_name)

    def _acquire_non_light(self, blocking: bool = False) -> bool:
        if self._non_light_cap <= 0:
            return True
        if self._use_distributed:
            return self._acquire_distributed('__non_light__', blocking)
        gate = self._non_light_gate
        if gate is None:
            return True
        return gate.acquire(blocking)

    def _release_non_light(self) -> None:
        if self._non_light_cap <= 0:
            return
        if self._use_distributed:
            self._release_distributed('__non_light__')
            return
        gate = self._non_light_gate
        if gate is not None:
            gate.release()
    
    def get_queue_limit(self, queue_name: str) -> int:
        """Возвращает лимит для очереди (0 = без ограничений)"""
        return self._queue_limits.get(queue_name, 0)
    
    def get_retry_delay(self) -> int:
        """Возвращает задержку перед повторной попыткой"""
        return self._retry_delay
    
    def acquire(self, queue_name: str, blocking: bool = False) -> bool:
        """
        Пытается захватить слот для выполнения задачи в очереди.
        
        Args:
            queue_name: Имя очереди
            blocking: Ждать освобождения слота
            
        Returns:
            True если слот захвачен, False если лимит превышен
        """
        if self.is_paused(queue_name):
            return False
        needs_class = self._needs_non_light_slot(queue_name)
        limit = self._queue_limits.get(queue_name, 0)
        if not needs_class and limit <= 0:
            return True

        if needs_class and not self._acquire_non_light(blocking):
            return False

        if limit <= 0:
            return True

        if self._use_distributed:
            acquired = self._acquire_distributed(queue_name, blocking)
        else:
            acquired = self._acquire_local(queue_name, blocking)
        if not acquired and needs_class:
            self._release_non_light()
        return acquired
    
    def release(self, queue_name: str):
        """
        Освобождает слот после выполнения задачи.
        
        Args:
            queue_name: Имя очереди
        """
        needs_class = self._needs_non_light_slot(queue_name)
        limit = self._queue_limits.get(queue_name, 0)
        if needs_class:
            self._release_non_light()
        if limit <= 0:
            return
        
        if self._use_distributed:
            self._release_distributed(queue_name)
        else:
            self._release_local(queue_name)
    
    def _acquire_local(self, queue_name: str, blocking: bool = False) -> bool:
        """Локальный захват через threading.Semaphore"""
        semaphore = self._semaphores.get(queue_name)
        if semaphore is None:
            return True
        
        acquired = semaphore.acquire(blocking)
        if acquired:
            logger.debug(f"Очередь {queue_name}: слот захвачен (local)")
        else:
            logger.debug(f"Очередь {queue_name}: лимит превышен (local)")
        return acquired
    
    def _release_local(self, queue_name: str):
        """Локальное освобождение через threading.Semaphore"""
        semaphore = self._semaphores.get(queue_name)
        if semaphore is not None:
            semaphore.release()
            logger.debug(f"Очередь {queue_name}: слот освобожден (local)")
    
    def _ensure_counter_key(self, cache_key: str) -> None:
        """Создаёт счётчик в cache, если ключа ещё нет (incr/decr Redis иначе падают)."""
        cache.add(cache_key, 0, self._cache_ttl)

    def _acquire_distributed(self, queue_name: str, blocking: bool = False) -> bool:
        """Распределенный захват через Django cache (требует Redis backend с incr)."""
        cache_key = f'celery:queue_concurrency:{queue_name}'
        if queue_name == '__non_light__':
            limit = self._non_light_cap
        else:
            limit = self._queue_limits.get(queue_name, 0)

        try:
            self._ensure_counter_key(cache_key)
            try:
                new_value = cache.incr(cache_key)
            except ValueError:
                # Ключ исчез между add и incr (TTL/очистка) — создаём заново
                cache.set(cache_key, 1, self._cache_ttl)
                new_value = 1

            if new_value is None:
                cache.set(cache_key, 1, self._cache_ttl)
                new_value = 1

            if new_value > limit:
                try:
                    cache.decr(cache_key)
                except ValueError:
                    cache.set(cache_key, 0, self._cache_ttl)
                logger.debug(
                    f"Очередь {queue_name}: лимит превышен ({new_value}/{limit}) (distributed)"
                )
                return False

            logger.debug(
                f"Очередь {queue_name}: слот захвачен ({new_value}/{limit}) (distributed)"
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка при захвате слота для очереди {queue_name}: {e}")
            return True  # При ошибке разрешаем выполнение
    
    def _release_distributed(self, queue_name: str):
        """Распределенное освобождение через Django cache"""
        cache_key = f'celery:queue_concurrency:{queue_name}'
        try:
            self._ensure_counter_key(cache_key)
            try:
                current = cache.decr(cache_key)
            except ValueError:
                cache.set(cache_key, 0, self._cache_ttl)
                current = 0
            if current is None or current < 0:
                cache.set(cache_key, 0, self._cache_ttl)
            logger.debug(f"Очередь {queue_name}: слот освобожден (distributed)")
        except Exception as e:
            logger.error(f"Ошибка при освобождении слота для очереди {queue_name}: {e}")
    
    def get_current_count(self, queue_name: str) -> int:
        """
        Возвращает текущее количество выполняемых задач в очереди.
        
        Args:
            queue_name: Имя очереди
            
        Returns:
            Количество выполняемых задач
        """
        if self._use_distributed:
            cache_key = f'celery:queue_concurrency:{queue_name}'
            return cache.get(cache_key, 0)
        else:
            semaphore = self._semaphores.get(queue_name)
            if semaphore is None:
                return 0
            return int(getattr(semaphore, 'held', 0))
    
    def get_all_limits(self) -> Dict[str, int]:
        """Возвращает все настроенные лимиты"""
        return self._queue_limits.copy()
    
    def has_limit(self, queue_name: str) -> bool:
        """Проверяет, есть ли лимит для очереди"""
        if self.is_paused(queue_name):
            return True
        if queue_name in self._queue_limits and self._queue_limits[queue_name] > 0:
            return True
        return self._needs_non_light_slot(queue_name)


# Глобальный экземпляр менеджера
queue_concurrency_manager = QueueConcurrencyManager()

# Словарь для отслеживания захваченных слотов по task_id
# {task_id: queue_name}
_task_acquired_slots: Dict[str, str] = {}
_task_slots_lock = threading.Lock()


def _get_task_queue_from_request(request) -> Optional[str]:
    """
    Определяет имя очереди для задачи из request.
    
    Args:
        request: Request объект задачи Celery
        
    Returns:
        Имя очереди или None
    """
    try:
        # Пытаемся получить из delivery_info
        delivery_info = getattr(request, 'delivery_info', None)
        if delivery_info:
            routing_key = delivery_info.get('routing_key')
            if routing_key:
                return routing_key
        
        # Пытаемся получить из имени задачи
        task_name = getattr(request, 'task', None)
        if task_name and 'modules.' in task_name:
            parts = task_name.split('.')
            # modules.my_module.api.tasks.run_example_task
            for i, part in enumerate(parts):
                if part == 'modules' and i + 1 < len(parts):
                    return parts[i + 1]
        
        return None
    except Exception as e:
        logger.debug(f"Не удалось определить очередь для задачи: {e}")
        return None


def create_concurrency_limited_task_class(base_task_class):
    """
    Создает класс задачи с ограничением параллелизма.
    
    Этот класс переопределяет __call__ чтобы проверять лимит ПЕРЕД выполнением задачи.
    Если лимит превышен, задача откладывается через retry() и НЕ выполняется.
    
    Args:
        base_task_class: Базовый класс Task из Celery
        
    Returns:
        Новый класс задачи с ограничением параллелизма
    """
    
    class ConcurrencyLimitedTask(base_task_class):
        """
        Task class с автоматическим ограничением параллелизма по очередям.
        """
        
        def __call__(self, *args, **kwargs):
            """
            Переопределяем __call__ для проверки лимита ДО выполнения задачи.
            """
            # Определяем очередь
            queue_name = _get_task_queue_from_request(self.request)
            
            # Проверяем, есть ли лимит для этой очереди
            if queue_name and queue_concurrency_manager.has_limit(queue_name):
                # Пытаемся захватить слот
                if not queue_concurrency_manager.acquire(queue_name, blocking=False):
                    # Лимит превышен - задача будет повторена
                    limit = queue_concurrency_manager.get_queue_limit(queue_name)
                    retry_delay = queue_concurrency_manager.get_retry_delay()
                    logger.info(
                        f"Очередь {queue_name}: лимит ({limit}) превышен. "
                        f"Задача {self.request.id} отложена на {retry_delay}с."
                    )
                    # Вызываем retry с max_retries=None чтобы НЕ учитывать в общем лимите retry задачи
                    # Это специальный retry для ограничения параллелизма, не для ошибок
                    raise self.retry(countdown=retry_delay, max_retries=None)
                
                # Слот захвачен - запоминаем для освобождения
                task_id = self.request.id
                with _task_slots_lock:
                    _task_acquired_slots[task_id] = queue_name
                logger.debug(f"Задача {task_id}: захвачен слот для очереди {queue_name}")
                
                try:
                    # Выполняем задачу
                    return super().__call__(*args, **kwargs)
                finally:
                    # Освобождаем слот
                    with _task_slots_lock:
                        _task_acquired_slots.pop(task_id, None)
                    queue_concurrency_manager.release(queue_name)
                    logger.debug(f"Задача {task_id}: освобожден слот для очереди {queue_name}")
            else:
                # Нет ограничений - выполняем как обычно
                return super().__call__(*args, **kwargs)
    
    return ConcurrencyLimitedTask


def setup_concurrency_limited_tasks(celery_app):
    """
    Настраивает автоматическое ограничение параллелизма для всех задач Celery.
    
    Заменяет базовый класс Task на ConcurrencyLimitedTask, который автоматически
    проверяет лимиты параллелизма перед выполнением каждой задачи.
    
    Args:
        celery_app: Экземпляр Celery приложения
        
    Использование:
        from src.core.utils.celery.concurrency import setup_concurrency_limited_tasks
        setup_concurrency_limited_tasks(celery_app)
    """
    # Создаем новый класс задачи на основе текущего
    ConcurrencyLimitedTask = create_concurrency_limited_task_class(celery_app.Task)
    
    # Заменяем базовый класс задачи
    celery_app.Task = ConcurrencyLimitedTask
    
    logger.info("Автоматическое ограничение параллелизма задач активировано")


def with_queue_limit(queue_name: Optional[str] = None):
    """
    Декоратор для ограничения параллелизма задач в очереди.
    
    Использование:
        @shared_task(bind=True)
        @with_queue_limit('my_module')
        def my_task(self, ...):
            # Задача автоматически ограничена по параллелизму
    
    Args:
        queue_name: Имя очереди. Если не указано, берется из routing_key задачи.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # Определяем имя очереди
            effective_queue = queue_name
            if effective_queue is None:
                # Пытаемся получить из delivery_info
                try:
                    effective_queue = self.request.delivery_info.get('routing_key', 'default')
                except AttributeError:
                    effective_queue = 'default'
            
            # Проверяем лимит
            if not queue_concurrency_manager.acquire(effective_queue, blocking=False):
                retry_delay = queue_concurrency_manager.get_retry_delay()
                limit = queue_concurrency_manager.get_queue_limit(effective_queue)
                logger.warning(
                    f"Очередь {effective_queue}: лимит ({limit}) превышен. "
                    f"Задача {self.request.id} будет повторена через {retry_delay}с."
                )
                raise self.retry(countdown=retry_delay)
            
            try:
                return func(self, *args, **kwargs)
            finally:
                queue_concurrency_manager.release(effective_queue)
        
        return wrapper
    return decorator


class QueueLimitContext:
    """
    Контекстный менеджер для ограничения параллелизма задач.
    
    Использование:
        with QueueLimitContext('my_module', task=self) as acquired:
            if not acquired:
                raise self.retry(countdown=60)
            # Выполнение задачи
    """
    
    def __init__(self, queue_name: str, task=None, blocking: bool = False):
        self.queue_name = queue_name
        self.task = task
        self.blocking = blocking
        self.acquired = False
    
    def __enter__(self) -> bool:
        self.acquired = queue_concurrency_manager.acquire(self.queue_name, self.blocking)
        return self.acquired
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            queue_concurrency_manager.release(self.queue_name)
        return False

