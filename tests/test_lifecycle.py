"""Lifecycle extraction tests."""
import asyncio
import sys
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unified_bridge as ub  # noqa: E402
import arena.lifecycle as lifecycle_module  # noqa: E402
from arena.app_keys import (  # noqa: E402
    APP_CFG,
    APP_FILE_WATCH_LOOP,
    APP_LOG_CLEANUP,
    APP_MISSION_SCHEDULE_LOOP,
    APP_TASK_RUNNER,
)
from arena.lifecycle import LifecycleContext, make_lifecycle  # noqa: E402


def _ctx(events, executor=None, slow_executor=None):
    executor = executor or ThreadPoolExecutor(max_workers=1)
    slow_executor = slow_executor or ThreadPoolExecutor(max_workers=1)

    async def dummy_loop(app):
        try:
            while True:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    async def stop_grpc():
        events.append("stop_grpc")

    async def stop_cluster():
        events.append("stop_cluster")

    return LifecycleContext(
        executor=executor,
        slow_executor=slow_executor,
        init_memory_db=lambda: events.append("init_memory"),
        task_runner_loop=dummy_loop,
        log_cleanup_loop=dummy_loop,
        file_watch_loop=dummy_loop,
        get_mission_schedule_loop=lambda: None,
        start_watchdog=lambda: events.append("start_watchdog"),
        stop_watchdog=lambda: events.append("stop_watchdog"),
        stop_cdp_watcher=lambda: events.append("stop_cdp"),
        cdp_state={"manager": None},
        stop_grpc_server=stop_grpc,
        stop_cluster_heartbeat=stop_cluster,
        get_shutdown_event=lambda: None,
        version="test",
        log_info=lambda *args, **kwargs: None,
        log_debug=lambda *args, **kwargs: None,
    )


def test_unified_lifecycle_bindings():
    assert ub.on_startup.__module__ == "arena.lifecycle"
    assert ub.on_cleanup.__module__ == "arena.lifecycle"
    assert ub._signal_handler.__module__ == "arena.lifecycle"


def test_lifecycle_startup_cleanup_flow():
    events = []
    executor = ThreadPoolExecutor(max_workers=1)
    slow_executor = ThreadPoolExecutor(max_workers=1)
    runtime = make_lifecycle(_ctx(events, executor, slow_executor))
    app = web.Application()
    app[APP_CFG] = {"max_concurrent": 2}

    asyncio.run(runtime.on_startup(app))
    assert "init_memory" in events
    assert "start_watchdog" in events
    assert app.get(APP_TASK_RUNNER) is not None
    assert app.get(APP_LOG_CLEANUP) is not None
    assert app.get(APP_FILE_WATCH_LOOP) is not None
    assert app.get(APP_MISSION_SCHEDULE_LOOP) is None
    assert app[APP_CFG]["semaphore"]

    asyncio.run(runtime.on_cleanup(app))
    assert "stop_watchdog" in events
    assert "stop_cdp" in events
    assert "stop_grpc" in events
    assert "stop_cluster" in events



def test_project_safe_lifecycle_disables_active_background_executors(monkeypatch):
    events = []
    executor = ThreadPoolExecutor(max_workers=1)
    slow_executor = ThreadPoolExecutor(max_workers=1)

    async def forbidden_loop(_app):
        events.append("FORBIDDEN_LOOP_STARTED")
        raise AssertionError("project-safe mode started an active background executor")

    def mission_lookup():
        events.append("MISSION_LOOKUP")
        return forbidden_loop

    def tunnel_hook():
        events.append("TUNNEL_AUTOSTART")
        raise AssertionError("project-safe mode called a tunnel autostart hook")

    async def forbidden_subprocess(*_args, **_kwargs):
        events.append("YDO_TOOLD")
        raise AssertionError("project-safe mode attempted to start ydotoold")

    base = _ctx(events, executor, slow_executor)
    ctx = replace(
        base,
        task_runner_loop=forbidden_loop,
        file_watch_loop=forbidden_loop,
        get_mission_schedule_loop=mission_lookup,
        cloudflared_autostart=tunnel_hook,
        ngrok_autostart=tunnel_hook,
        tailscale_autostart=tunnel_hook,
        bore_autostart=tunnel_hook,
    )
    runtime = make_lifecycle(ctx)
    app = web.Application()
    app[APP_CFG] = {"max_concurrent": 2}

    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setattr(
        lifecycle_module,
        "spawn_background",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("project-safe mode spawned post-update/tunnel background work")
        ),
    )
    monkeypatch.setattr(lifecycle_module.shutil, "which", lambda _name: "ydotoold")
    monkeypatch.setattr(
        lifecycle_module.asyncio,
        "create_subprocess_exec",
        forbidden_subprocess,
    )

    asyncio.run(runtime.on_startup(app))

    assert app.get(APP_TASK_RUNNER) is None
    assert app.get(APP_FILE_WATCH_LOOP) is None
    assert app.get(APP_MISSION_SCHEDULE_LOOP) is None
    assert app.get(APP_LOG_CLEANUP) is not None
    assert "MISSION_LOOKUP" not in events
    assert "TUNNEL_AUTOSTART" not in events
    assert "YDO_TOOLD" not in events
    assert "FORBIDDEN_LOOP_STARTED" not in events
    assert "start_watchdog" in events

    asyncio.run(runtime.on_cleanup(app))
    assert "stop_watchdog" in events
    assert "stop_cdp" in events
    assert "stop_grpc" in events
    assert "stop_cluster" in events
