# pydantic-graph-redis-persistance

[![PyPI - Version](https://img.shields.io/pypi/v/pydantic-graph-redis-persistance)](https://pypi.org/project/pydantic-graph-redis-persistance/)

## Installation

The package is available on [PyPi](https://pypi.org/project/pydantic-graph-persistance-redis/)
and can be install via [pip](https://pypi.org/project/pip/) or any
other package manager:

```bash
pip install pydantic-graph-redis-persistance
```

## Usage

The responsability of creating a proper asynchronous Redis client is yours,
then you can simply create a state persistance instance for your graph run:

```python
from pydantic_graph.persistance.redis import RedisStatePersistance
from redis.asyncio import Redis

redis = Redis(...)
run_id = "my_unique_run_id"
persistance = RedisStatePersistance(redis, run_id)

# You can now use your persistance through any graph run :).
```

## Locking

To provide a quick and simple implementation, the current version
use a deprecated [Redis](https://redis.io) locking mecanism through
`SETNX` operation. If you want you can implement your own locking
mecanism (for instance redlock) or add a custom timeout to the lock
you can provide you own `AbstractRedisStateLock` instance:

```python
from pydantic_graph.persistance.redis import NXRedisStateLock
from pydantic_graph.persistance.redis import RedisStatePersistance

redis = Redis(...)
lock = NXRedisStateLock(
    lock_id="my_custom_lock_id",
    redis=redis,
    timeout=10000.0,
)
run_id = "my_unique_run_id"
persistance = RedisStatePersistance(redis, run_id, redis_state_lock=lock)
```
