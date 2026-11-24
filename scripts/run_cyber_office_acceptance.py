#!/usr/bin/env python3
"""Optional Chromium acceptance: real loopback API + disposable SQLite, zero provider calls.

Requires Playwright and an installed Chromium. Credentials are generated only for the disposable
fixture, held in memory, and never printed or written to browser storage or screenshot metadata.
"""

from __future__ import annotations

import argparse
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from project_supervisor.api import APISettings, create_app
from project_supervisor.autonomous_host import AutonomousHostRepository
from project_supervisor.autonomy import GoalService
from project_supervisor.domain import (
    Harness,
    ModelDescriptor,
    NodeState,
    Provider,
    ResourceState,
    WorkerSnapshot,
    WorkerState,
)
from project_supervisor.heartbeat import HeartbeatConfig, MetabolicHeartbeat, SleepPolicy, utc_time
from project_supervisor.memory import MemoryDraft, MemoryRepository
from project_supervisor.store import StateStore


def fixture(root: Path) -> StateStore:
    store = StateStore(root / "office.db")
    store.create_project(
        project_id="fixture", name="Office test", root_path=str(root), goal="Observe"
    )
    store.upsert_node(
        node_id="test-node",
        hostname="fixture",
        display_name="Test fixture",
        role="worker",
        state=NodeState.ONLINE,
    )
    for index, (harness, provider, name) in enumerate(
        (
            (Harness.CODEX, Provider.OPENAI, "Codex"),
            (Harness.CLAUDE_CODE, Provider.ANTHROPIC, "Claude"),
            (Harness.GROK_BUILD, Provider.XAI, "Grok"),
            (Harness.GOOGLE_AGY, Provider.GOOGLE, "AGY"),
        )
    ):
        store.upsert_worker(
            WorkerSnapshot(
                id=f"worker-{name.lower()}",
                node_id="test-node",
                harness=harness,
                provider=provider,
                model=ModelDescriptor("fixture", f"{name} · 测试替身", provider),
                state=WorkerState.IDLE if index < 3 else WorkerState.OFFLINE,
                node_state=NodeState.ONLINE,
                resource_state=ResourceState.UNKNOWN,
                capabilities=frozenset({"reasoning"}),
                code_write_allowed=False,
                privacy_allowed=False,
            )
        )
    goals = GoalService(store)
    for name in ("atlas", "chronos", "archive"):
        goals.create_goal(project_id="fixture", intent="Read-only fixture", goal_id=f"goal-{name}")
    goals.stop("goal-archive", reason="Fixture research ended")
    event = next(e for e in store.list_events(limit=100) if e["kind"] == "goalStopped")
    memory = MemoryRepository(store)
    memory.reflect_terminal_goals()
    memory.record(
        MemoryDraft(
            source_goal_id="goal-archive",
            source_event_id=event["event_id"],
            kind="conclusion",
            content="定时唤醒只表达检查机会；是否运行仍由租约、人类控制与预算共同裁决。",
        )
    )
    memory.record(
        MemoryDraft(
            source_goal_id="goal-archive",
            source_event_id=event["event_id"],
            kind="failureLesson",
            content='<img src=x onerror="window.fixtureXSS=1"> is untrusted text',
        )
    )
    hosts = AutonomousHostRepository(store)
    hosts.register_host(
        "host-fixture",
        process_id=1,
        metadata={
            "leaseTTLSeconds": 30.0,
            "exploration": {
                "enabled": True,
                "exploration_budget_cap": 2,
                "cooldown_seconds": 3600.0,
            },
        },
    )
    hosts.heartbeat_host("host-fixture", active_goal_count=0)
    heartbeat = MetabolicHeartbeat(store)
    now = datetime.now(UTC)
    heartbeat.enter_sleep(
        "goal-chronos",
        SleepPolicy(
            wake_reason="timer",
            next_wake_at=utc_time(now - timedelta(seconds=1)),
        ),
    )
    claims = heartbeat.tick("host-fixture", config=HeartbeatConfig(), lease_ttl_seconds=30.0)
    assert len(claims) == 1
    hosts.release_goal(claims[0])
    heartbeat.enter_sleep(
        "goal-chronos",
        SleepPolicy(
            wake_reason="timer",
            next_wake_at=utc_time(now + timedelta(minutes=15)),
        ),
    )
    for _ in range(12):
        heartbeat.tick("host-fixture", config=HeartbeatConfig(), lease_ttl_seconds=30.0)
    return store


@contextmanager
def serve(store: StateStore, settings: APISettings):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(store, settings),
                log_level="error",
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("test loopback server did not start")
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "test server did not shut down"


def check_motion(page, store: StateStore) -> None:
    from playwright.sync_api import expect

    page.emulate_media(reduced_motion="no-preference")
    for section in ("workers-section", "memory-section", "controls-section", "overview"):
        link = page.locator(f'nav a[href="#{section}"]')
        link.click()
        expect(link).to_have_attribute("aria-current", "location")
        assert page.locator(f"#{section} > :first-child").evaluate(
            "el => getComputedStyle(el, '::after').animationName === 'register-line'"
        )
    page.locator("#connect-open").click()
    expect(page.locator("#bearer")).to_be_focused()
    assert page.locator("#connect-title").evaluate(
        "el => getComputedStyle(el, '::after').animationName === 'register-line'"
    )
    page.locator("#bearer").press("Escape")
    expect(page.locator(".reticle")).to_have_css("animation-name", "orbit")
    page.evaluate("""() => {
        const ring = document.querySelector('.upload-ring');
        ring.dataset.waves = '0';
        ring.addEventListener('animationstart', () => {
            ring.dataset.waves = String(Number(ring.dataset.waves) + 1);
        });
    }""")
    hosts = AutonomousHostRepository(store)
    hosts.heartbeat_host("host-fixture", active_goal_count=0)
    GoalService(store).create_goal(project_id="fixture", intent="Observe pulse", goal_id="pulse")
    heartbeat = MetabolicHeartbeat(store)
    heartbeat.enter_sleep(
        "pulse", SleepPolicy(wake_reason="timer", next_wake_at=utc_time(datetime.now(UTC)))
    )
    claims = heartbeat.tick("host-fixture", config=HeartbeatConfig(), lease_ttl_seconds=30.0)
    assert len(claims) == 1
    expect(page.locator(".upload-ring")).to_have_attribute("data-waves", "1")
    expect(page.locator(".portrait.upload")).to_have_count(0)
    page.locator("#refresh").click()
    expect(page.locator("#refresh")).to_be_enabled()
    expect(page.locator(".upload-ring")).to_have_attribute("data-waves", "1")
    hosts.release_goal(claims[0])
    heartbeat.tick("host-fixture", config=HeartbeatConfig(), lease_ttl_seconds=30.0)
    expect(page.locator("#tick-end")).to_have_text(f"#{heartbeat.list_ticks()[0]['sequence']}")
    expect(page.locator(".upload-ring")).to_have_attribute("data-waves", "1")
    page.emulate_media(reduced_motion="reduce")
    expect(page.locator(".reticle")).to_have_css("animation-name", "none")
    assert page.evaluate("document.getAnimations().length") == 0


def check(browser, store: StateStore, base: str, screenshots: Path | None) -> None:
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
    page = context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(base + "/office/")
    expect(page.locator("#notice")).to_contain_text("授权未通过")
    expect(page.locator('[data-action="stop"]')).to_be_disabled()
    _, token = store.issue_api_token(
        label="disposable acceptance", scopes={"observe:read", "goal:control"}
    )
    page.get_by_role("button", name="连接设置").click()
    if screenshots:
        page.screenshot(path=str(screenshots / "desktop-connection.png"))
    page.locator("#bearer").fill(token)
    page.locator("#connect-form").get_by_role("button", name="连接", exact=True).click()
    expect(page.locator(".worker-card")).to_have_count(4)
    expect(page.locator("#connection-state")).to_have_text("神经链路 · 已接驳")
    for title, subtitle in (
        ("01 生物监测", "BIOMONITOR · RELIC"),
        ("02 魔偶阵列", "DAEMONS · CYBERDECK"),
        ("03 分离芯片", "SHARDS · DATABASE"),
        ("04 入侵协议", "BREACH PROTOCOL · ICE"),
    ):
        link = page.get_by_role("navigation").get_by_role("link", name=f"{title} {subtitle}")
        expect(link).to_be_visible()
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    rail = page.locator(".masthead").bounding_box()
    main = page.get_by_role("main").bounding_box()
    assert rail and main and rail["x"] + rail["width"] <= main["x"]
    expect(page.locator(".spec-code")).to_be_visible()
    expect(page.locator(".metabolic .plate-heading")).to_contain_text("LOCAL · ZERO-TOKEN PING")
    expect(page.locator(".budget .plate-heading")).to_contain_text("RAM · BLACKWALL QUOTA")
    assert page.locator(".reticle").evaluate("img => img.complete && img.naturalWidth > 0")
    expect(page.locator("#authority-mode")).to_have_text("接入仓已解锁")
    expect(page.locator(".legend")).to_have_text("静默 (PING) 唤醒 (UPLOAD)")
    expect(page.locator(".portrait figcaption")).to_have_text("ENGRAM · 01 // 愚者")
    expect(page.locator("#wake-count")).to_have_text("1")
    assert page.locator("#bearer").input_value() == ""
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not page.evaluate("document.cookie")
    assert page.locator("#memory-feed img").count() == 0
    assert page.evaluate("window.fixtureXSS") is None
    page.locator("#worker-filter").select_option("offline")
    expect(page.locator(".worker-card")).to_have_count(1)
    page.locator("#worker-filter").select_option("running")
    expect(page.locator("#workers .empty")).to_be_visible()
    page.locator("#worker-filter").select_option("all")
    page.get_by_role("tab", name="长期记忆", exact=True).press("ArrowRight")
    expect(page.get_by_role("tab", name="反思摘要", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    page.get_by_role("tab", name="长期记忆", exact=True).click()
    for state, label, button in [
        ("paused", "已暂停", "黑墙封锁（禁止自主漫游）"),
        ("open", "开放", "开启黑墙通道"),
    ]:
        page.get_by_role("button", name=button, exact=True).click()
        page.locator("#reason").fill("Operator testing global exploration control")
        page.locator("#confirm-control").click()
        expect(page.locator("#receipt")).to_contain_text(f"STATE {state}")
        expect(page.locator("#exploration-control-state")).to_contain_text(label)
    page.locator("#goal-select").select_option("goal-atlas")
    if screenshots:
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(screenshots / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if screenshots:
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
    page.locator('[data-action="hard"]').click()
    expect(page.locator("dialog.hazard")).to_be_visible()
    if screenshots:
        page.screenshot(path=str(screenshots / "mobile-confirm.png"))
    page.locator("#reason").press("Escape")
    page.set_viewport_size({"width": 1440, "height": 1100})
    assert GoalService(store).get_goal("goal-atlas")["state"] == "created"
    for label, expected in (
        ("挂起队列", "softPaused"),
        ("重新接驳", "running"),
        ("注入魔偶", "running"),
        ("系统重置", "hardPaused"),
        ("强制断连", "stopped"),
    ):
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.locator("#confirm-title")).to_contain_text(label)
        page.locator("#reason").fill("Human acceptance fixture")
        page.locator("#confirm-control").click()
        expect(page.locator("#receipt")).to_contain_text(f"STATE {expected}")
        assert GoalService(store).get_goal("goal-atlas")["state"] == expected
    expect(page.locator('[data-action="stop"]')).to_be_disabled()
    # A server-side control must refresh via the event stream without a manual page refresh.
    page.locator("#goal-select").select_option("goal-chronos")
    GoalService(store).pause("goal-chronos", "hard", reason="Stream refresh fixture")
    expect(page.locator("#goal-detail")).to_contain_text("hardPaused", timeout=3000)
    page.get_by_role("tab", name="事件日志", exact=True).click()
    expect(page.locator("#events-feed")).to_contain_text("Goal hard pause requested")
    check_motion(page, store)
    page.get_by_role("button", name="连接设置").click()
    page.locator("#bearer").fill("invalid-fixture-token")
    page.locator("#connect-form").get_by_role("button", name="连接", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("授权未通过")
    expect(page.locator(".worker-card")).to_have_count(0)
    expect(page.locator('[data-action="stop"]')).to_be_disabled()
    assert not errors, errors
    assert all(url.startswith(base + "/") for url in requests)
    assert not any("token=" in url for url in requests)
    context.close()


def main() -> None:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-binary", help="explicit installed Chromium executable")
    parser.add_argument("--screenshots", type=Path, help="new directory for fixture screenshots")
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="office-acceptance-") as folder:
        store = fixture(Path(folder))
        with (
            serve(
                store, APISettings(allow_goal_mutations=True, poll_interval_seconds=0.05)
            ) as base,
            sync_playwright() as playwright,
        ):
            browser = playwright.chromium.launch(executable_path=args.browser_binary)
            try:
                check(browser, store, base, args.screenshots)
                empty = StateStore(Path(folder) / "empty.db")
                with serve(empty, APISettings(allow_unauthenticated_loopback=True)) as empty_url:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.goto(empty_url + "/office/")
                    from playwright.sync_api import expect

                    expect(page.locator("#workers .empty")).to_contain_text("没有已注册")
                    expect(page.locator("#quota-summary")).to_have_text("内存配额未上报 · 保守锁定")
                    expect(page.locator("#exploration-status")).to_have_text("自发探索未启用")
                    expect(page.locator('[data-action="stop"]')).to_be_disabled()
                    page.close()
            finally:
                browser.close()
    print(
        "Cyber Office acceptance passed: responsive, projections, stream, "
        "auth, controls, XSS, empty state"
    )


if __name__ == "__main__":
    main()
