from typing import List, ContextManager, Optional
import collections
import functools
import inspect
import multiprocessing
from datetime import datetime, timedelta, timezone
import math
import time
import asyncio
import httpx
import src.lang as lang

logfile = open("./log.txt", "a", encoding="utf-8")


def rtruncate(string, n):
    """Truncate `n` chars in the `string` from the right

    :param string: string
    :param n: number of chars to be truncated
    """
    return string[:-n]


def datetime_str(d: datetime) -> str:
    """Format datetime
    """
    return rtruncate(d.strftime("%Y-%m-%d, %H:%M:%S.%f"), 4)


def utc_to_local(d: datetime) -> datetime:
    """Convert utc datetime to local
    """
    return d.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def fprint(*args, **kwargs):
    print(*args, **kwargs)
    kwargs['file'], kwargs['flush'] = logfile, True
    print(datetime_str(datetime.now()), *args, **kwargs)


def apy(apr: float):
    return math.exp(apr) - 1


def utcfrommillisecs(millisecs: str):
    return datetime.utcfromtimestamp(int(millisecs) / 1000)


def num_decimals(f: str):
    """Number of decimals
    """
    return len(f[f.find('.'):]) - 1


def float_str(f: float, decimals: int):
    """String of float up to certain decimals
    """
    return f'{f:.{decimals}f}'


def round_to(number, fraction) -> float:
    """Return the quotient of `number` and `fraction`
    """
    fs = f'{fraction:.18f}'.rstrip('0')
    decimals = num_decimals(fs)
    if decimals > 0:
        return round(number / fraction // 1 * fraction, decimals)
    else:
        return round(number / fraction // 1 * fraction)


class REST_Semaphore(asyncio.Semaphore):
    """A custom semaphore to be used with REST API with velocity limit under asyncio
    """

    def __init__(self, value: int, interval: int):
        """控制REST API访问速率

        :param value: API limit
        :param interval: Reset interval
        """
        super().__init__(value)
        # Queue of inquiry timestamps
        self._inquiries = collections.deque(maxlen=value)
        self._loop = asyncio.get_event_loop()
        self._interval = interval

    def __repr__(self):
        return f'API velocity: {self._inquiries.maxlen} inquiries/{self._interval}s'

    async def acquire(self):
        await super().acquire()
        if self._inquiries:
            timelapse = time.monotonic() - self._inquiries.popleft()
            # Wait until interval has passed since the first inquiry in queue returned.
            if timelapse < self._interval:
                await asyncio.sleep(self._interval - timelapse)
        return True

    def release(self):
        self._inquiries.append(time.monotonic())
        super().release()


class p_Semaphore(ContextManager):
    """A custom semaphore to be used with REST API with velocity limit by processes
    """

    def __init__(self, value: int, interval: int):
        """控制REST API并发连接

        :param value: API limit
        :param interval: Reset interval
        """
        self._interval = interval
        self._sem = multiprocessing.Semaphore(value)
        # Queue of inquiry timestamps
        self._inquiries = multiprocessing.Queue()

    def __enter__(self):
        self._sem.acquire()
        if self._inquiries.qsize():
            timelapse = time.monotonic() - self._inquiries.get()
            # Wait until interval has passed since the first inquiry in queue returned.
            if timelapse < self._interval:
                time.sleep(self._interval - timelapse)
        return True

    def __exit__(self, *args):
        self._inquiries.put(time.monotonic())
        self._sem.release()


def columned_output(res: List, header: str, ncols: int, form):
    len1 = len(res)
    nrows = len1 // ncols + 1
    headers = ''
    for j in range(ncols):
        headers += header
        if j < ncols - 1:
            headers += '\t'
    fprint(headers)
    for i in range(nrows):
        line = ''
        for j in range(ncols):
            if i + j * nrows < len1:
                n = res[i + j * nrows]
                line += form(n)
                if j < ncols - 1:
                    line += '\t'
        fprint(line)


async def get_with_limit(api, tag, max, limit=0, **kwargs):
    """Loop `api` until `limit` is reached

    :param api: api coroutine
    :param tag: tag used by after argument
    :param max: max number of results in a single request
    :param limit: number of entries
    :param kwargs: other arguments
    :return: List
    """
    # Number of entries is known.
    if limit > 0:
        res = temp = []
        while limit > 0:
            if limit <= max:
                if len(temp) == max:
                    # Last time
                    temp = await api(**kwargs, after=temp[max - 1][tag], limit=limit)
                else:
                    # First time
                    temp = await api(**kwargs, limit=limit)
            else:
                if len(temp) == max:
                    # Last time
                    temp = await api(**kwargs, after=temp[max - 1][tag], limit=max)
                else:
                    # First time
                    temp = await api(**kwargs, limit=max)
            res.extend(temp)
            limit -= max
    else:
        # First time
        res = temp = await api(**kwargs)
        # print(datetime_str(datetime.now()))
        # Results not exhausted
        while len(temp) == max:
            temp = await api(**kwargs, after=temp[max - 1][tag])
            # print(datetime_str(datetime.now()))
            res.extend(temp)
    return res


def debug_timer(cls):
    """Decorator for debug and timing
    """
    if isinstance(cls, type):
        # print(f'{cls.__name__} is class')
        old_init = getattr(cls, '__init__')

        @functools.wraps(old_init)
        def __init__(self, *args, **kwargs):
            print(f"{cls.__name__} init started")
            begin = time.monotonic()
            old_init(self, *args, **kwargs)
            print(f"{cls.__name__}({self.coin}) init finished")
            end = time.monotonic()
            print(f"{cls.__name__} init takes {end - begin} s")

        cls.__init__ = __init__

        if old_await := getattr(cls, '__await__', False):
            @functools.wraps(old_await)
            def __await__(self):
                print(f"{cls.__name__}__await__ started")
                begin = time.monotonic()
                result = yield from old_await(self)
                print(f"{cls.__name__}__await__ finished")
                end = time.monotonic()
                print(f"{cls.__name__}__await__ takes {end - begin} s")
                return result

            cls.__await__ = __await__

        if old_del := getattr(cls, '__del__', False):
            @functools.wraps(old_del)
            def __del__(self):
                print(f"{cls.__name__} del started")
                old_del(self)
                print(f"{cls.__name__} del finished")

            cls.__del__ = __del__
        return cls
    elif asyncio.iscoroutinefunction(cls):
        # print(f"{cls.__name__} is coroutine function")

        @functools.wraps(cls)
        async def wrapper(*args, **kwargs):
            begin = time.monotonic()
            res = await cls(*args, **kwargs)
            end = time.monotonic()
            print(f"{cls.__name__} takes {end - begin} s")
            return res

        return wrapper
    elif inspect.isroutine(cls):
        # print(f"{cls.__name__} is function")

        @functools.wraps(cls)
        def wrapper(*args, **kwargs):
            begin = time.monotonic()
            res = cls(*args, **kwargs)
            end = time.monotonic()
            print(f"{cls.__name__} takes {end - begin} s")
            return res

        return wrapper
    else:
        # print(f"{cls.__name__} is nothing")
        return cls


def call_coroutine(cls):
    """Decorator to call `coro(*args, **kwargs)` in normal context
    and `await coro(*args, **kwargs)` in async context.
    """
    if asyncio.iscoroutinefunction(cls):
        # cls is a coroutine function.
        @functools.wraps(cls)
        def wrapper(*args, **kwargs):
            loop = asyncio.get_event_loop()
            # coro is a coroutine object.
            coro = cls(*args, **kwargs)
            # print(f"{cls.__name__} {asyncio.iscoroutine(coro)=}")
            if loop.is_running():
                # print("loop is running")
                # Return the coroutine object to be awaited.
                return coro
            else:
                # print("loop is not running")
                # Execute the coroutine object and return its result.
                return loop.run_until_complete(coro)

        return wrapper
    elif isinstance(cls, type):
        # cls is a class.
        if hasattr(cls, '__await__'):
            # Decorate the class into a construction function which cannot be inherited.
            # @functools.wraps(cls)
            # def wrapper(*args, **kwargs):
            #     # class instance
            #     ins = cls(*args, **kwargs)
            #
            #     loop = asyncio.get_event_loop()
            #     if loop.is_running():
            #         # Return the class instance to be awaited.
            #         pass
            #     else:
            #         # Initiate the class instance asynchronously.
            #         loop.run_until_complete(ins.__await__())
            #     return ins
            # return wrapper

            # Decorate the class into a class.
            old_init = getattr(cls, '__init__')

            @functools.wraps(old_init)
            def __init__(self, *args, **kwargs):
                old_init(self, *args, **kwargs)
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    pass
                else:
                    loop.run_until_complete(self.__await__())

            cls.__init__ = __init__
    return cls


async def ainput(loop, *args):
    return await asyncio.ensure_future(loop.run_in_executor(None, functools.partial(input, *args)))
