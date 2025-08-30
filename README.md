# pydantic-graph-redis-persistance

## Installation

The package is available on [PyPi]() and can be install via
[pip]() or any other package manager:

```bash
pip install pydantic-graph-redis-persistance
```

## Usage

The responsability of creating a proper asynchronous Redis client is yours,
then you can simply create a state persistance instance for your graph run:

```python
from pydantic_graph_persistance_redis import RedisStatePersistance
from redis.asyncio import Redis

redis = Redis(...)
run_id = "my_unique_run_id"
persistance = RedisStatePersistance(redis, run_id)

# You can now use your persistance through any graph run :).
```

## RedisStatePersistance parameters

## Locking

To provide a quick and simple implementation, the current version
use a deprecated [Redis]() locking mecanism through `SETNX`
operation. If you want you can implement your own locking mecanism
(for instance redlock).