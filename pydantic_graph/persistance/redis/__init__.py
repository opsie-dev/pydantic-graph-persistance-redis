import asyncio

from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from time import perf_counter
from types import TracebackType
from typing import Any
from typing import AsyncIterator
from typing import Type

from pydantic import TypeAdapter
from pydantic_graph import BaseNode
from pydantic_graph import End
from pydantic_graph import EndSnapshot
from pydantic_graph import NodeSnapshot
from pydantic_graph import Snapshot
from pydantic_graph import exceptions
from pydantic_graph.persistence import BaseStatePersistence
from pydantic_graph.persistence import RunEndT
from pydantic_graph.persistence import SnapshotStatus
from pydantic_graph.persistence import StateT
from pydantic_graph.persistence import build_snapshot_list_type_adapter
from redis.asyncio import Redis


@dataclass
class AbstractRedisStateLock(AbstractAsyncContextManager[None]):
    lock_id: str
    redis: Redis
    timeout: float | None = None

    @abstractmethod
    async def __aenter__(self) -> None:
        raise NotImplementedError


class NXRedisStateLock(AbstractRedisStateLock):
    async def _acquire_lock(self) -> None:
        while self.redis.setnx(self.lock_id, 1):
            await asyncio.sleep(0.01)

    async def __aenter__(self) -> None:
        await asyncio.wait_for(
            self._acquire_lock(),
            timeout=self.timeout,
        )

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.redis.delete(self.lock_id)
        if exc_value is not None:
            raise exc_value


class RedisStatePersistance(BaseStatePersistence[StateT, RunEndT]):
    def __init__(
        self,
        redis: Redis,
        run_id: str,
        *,
        redis_state_lock: AbstractRedisStateLock | None = None,
    ) -> None:
        self._redis = redis
        self._redis_state_lock: AbstractRedisStateLock
        if redis_state_lock is None:
            self._redis_state_lock = NXRedisStateLock(
                lock_id=f"lock.{run_id}",
                redis=redis,
            )
        else:
            self._redis_state_lock = redis_state_lock
        self._run_id = run_id
        self._snapshots_type_adapter: (
            TypeAdapter[list[Snapshot[StateT, RunEndT]]] | None
        ) = None

    async def snapshot_node(
        self,
        state: StateT,
        next_node: BaseNode[StateT, Any, RunEndT],
    ) -> None:
        await self._append_save(
            NodeSnapshot(
                state=state,
                node=next_node,
            ),
        )

    async def snapshot_node_if_new(
        self,
        snapshot_id: str,
        state: StateT,
        next_node: BaseNode[StateT, Any, RunEndT],
    ) -> None:
        async with self._redis_state_lock:
            snapshots = await self.load_all()
            if not any(s.id == snapshot_id for s in snapshots):  # pragma: no branch
                await self._append_save(
                    NodeSnapshot(
                        state=state,
                        node=next_node,
                    ),
                    lock=False,
                )

    async def snapshot_end(
        self,
        state: StateT,
        end: End[RunEndT],
    ) -> None:
        await self._append_save(
            EndSnapshot(
                state=state,
                result=end,
            ),
        )

    @asynccontextmanager
    async def record_run(self, snapshot_id: str) -> AsyncIterator[None]:
        async with self._redis_state_lock:
            snapshots = await self.load_all()
            try:
                snapshot = next(s for s in snapshots if s.id == snapshot_id)
            except StopIteration as e:
                raise LookupError(f"No snapshot found with id={snapshot_id!r}") from e
            assert isinstance(snapshot, NodeSnapshot), (
                "Only NodeSnapshot can be recorded"
            )
            exceptions.GraphNodeStatusError.check(snapshot.status)
            snapshot.status = "running"
            snapshot.start_ts = datetime.now(tz=timezone.utc)
            await self._save(snapshots)
        start = perf_counter()
        try:
            yield
        except Exception:
            duration = perf_counter() - start
            async with self._redis_state_lock:
                await self._after_run(
                    snapshot_id,
                    duration,
                    "error",
                )
            raise
        else:
            snapshot.duration = perf_counter() - start
            async with self._redis_state_lock:
                await self._after_run(
                    snapshot_id,
                    snapshot.duration,
                    "success",
                )

    async def load_next(self) -> NodeSnapshot[StateT, RunEndT] | None:
        async with self._redis_state_lock:
            snapshots = await self.load_all()
            if snapshot := next(
                (
                    s
                    for s in snapshots
                    if isinstance(s, NodeSnapshot) and s.status == "created"
                ),
                None,
            ):
                snapshot.status = "pending"
                await self._save(snapshots)
                return snapshot
        return None

    async def load_all(self) -> list[Snapshot[StateT, RunEndT]]:
        return await self._load()

    def should_set_types(self) -> bool:
        return self._snapshots_type_adapter is None

    def set_types(
        self,
        state_type: type[StateT],
        run_end_type: type[RunEndT],
    ) -> None:
        self._snapshots_type_adapter = build_snapshot_list_type_adapter(
            state_type,
            run_end_type,
        )

    async def _after_run(
        self,
        snapshot_id: str,
        duration: float,
        status: SnapshotStatus,
    ) -> None:
        snapshots = await self.load_all()
        snapshot = next(s for s in snapshots if s.id == snapshot_id)
        assert isinstance(snapshot, NodeSnapshot), "Only NodeSnapshot can be recorded"
        snapshot.duration = duration
        snapshot.status = status
        await self._save(snapshots)

    async def _append_save(
        self,
        snapshot: Snapshot[StateT, RunEndT],
        *,
        lock: bool = True,
    ) -> None:
        assert self._snapshots_type_adapter is not None, (
            "snapshots type adapter must be set"
        )
        async with AsyncExitStack() as stack:
            if lock:
                await stack.enter_async_context(self._redis_state_lock)
            snapshots = await self.load_all()
            snapshots.append(snapshot)
            await self._save(snapshots)

    async def _load(
        self,
    ) -> list[Snapshot[StateT, RunEndT]]:
        assert self._snapshots_type_adapter is not None, (
            "snapshots type adapter must be set"
        )
        value = await self._redis.get(self._run_id)
        if value is None:
            return []
        return self._snapshots_type_adapter.validate_json(value)

    async def _save(self, snapshots: list[Snapshot[StateT, RunEndT]]) -> None:
        assert self._snapshots_type_adapter is not None, (
            "snapshots type adapter must be set"
        )
        value = self._snapshots_type_adapter.dump_json(
            snapshots,
            indent=2,
        )
        await self._redis.set(self._run_id, value)
