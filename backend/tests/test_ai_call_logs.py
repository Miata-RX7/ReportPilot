"""AI 调用留痕（ai_call_logs）测试。

关注四件事：
1. 留痕是否「完整」：messages / 模型参数 / 响应原文 / 护栏重试的第二次请求 / 耗时
2. 不传 trace 时保持纯函数语义（不写库），传了才落库
3. 红线：base_url 去 userinfo、异常文本脱敏，表中绝不出现 api_key / Bearer
4. 查询接口的过滤、详情与鉴权
"""

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.ai_call import AiCallLog
from app.models.user import User
from app.schemas.metric import MetricFact
from app.services import ai_trace, runtime_config
from app.services.ai_insight import InsightConfig, generate_summary

AI_BASE = "http://ai.test/v1"
CHAT_URL = f"{AI_BASE}/chat/completions"


def make_fact(**kw) -> MetricFact:
    base = {
        "metric_id": 1,
        "name": "cpu_usage",
        "value": 35.0,
        "baseline_value": 30.0,
        "change_pct": 16.67,
        "unit": "%",
        "source": "生产 Zabbix",
        "object": "Linux servers",
        "formula": "avg(...)",
        "period": "2026-09",
    }
    base.update(kw)
    return MetricFact(**base)


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def ctx(**kw) -> ai_trace.AiCallContext:
    base = {
        "kind": "generate",
        "report_id": 7,
        "slot": "summary",
        "period": "2026-09",
        "actor_id": 1,
    }
    base.update(kw)
    return ai_trace.AiCallContext(**base)


@pytest.fixture
def ai_settings(monkeypatch: pytest.MonkeyPatch):
    # effective_settings() 只在覆盖层缓存为空时**返回 get_settings() 本体**，
    # 此时 monkeypatch 才生效；不清缓存的话，别的用例载入过覆盖项会让这里假降级。
    runtime_config.reset_cache()
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_base_url", AI_BASE)
    monkeypatch.setattr(settings, "ai_api_key", "test-ai-key")
    monkeypatch.setattr(settings, "ai_model", "test-model")
    return settings


@pytest_asyncio.fixture
async def log_db(monkeypatch: pytest.MonkeyPatch):
    """内存库 + 把 ai_trace 的独立 session 指向它。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ai_trace, "AsyncSessionLocal", factory)
    async with factory() as s:
        s.add(
            User(
                id=1,
                username="ops",
                email="ops@x.com",
                display_name="ops",
                source="local",
                role="admin",
                is_active=True,
            )
        )
        await s.commit()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def api(log_db):
    async def _get_db():
        async with log_db() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="ops",
        email="ops@x.com",
        display_name="ops",
        source="local",
        role="admin",
        is_active=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


async def _all(factory) -> list[AiCallLog]:
    async with factory() as s:
        return list((await s.scalars(select(AiCallLog).order_by(AiCallLog.id))).all())


# --------------------------------------------------------------------------
# 留痕内容
# --------------------------------------------------------------------------
class TestTraceContent:
    @respx.mock
    async def test_without_trace_writes_nothing(self, log_db, ai_settings) -> None:
        """不传 trace = 纯函数：只算不落库（升级前的调用方式仍是零副作用）。"""
        respx.post(CHAT_URL).mock(return_value=chat_response("CPU 使用率 35%。"))
        await generate_summary([make_fact()], "2026-09")
        assert await _all(log_db) == []

    @respx.mock
    async def test_success_records_full_request_and_response(
        self, log_db, ai_settings
    ) -> None:
        answer = "CPU 使用率 35%，环比上升 16.67%。"
        respx.post(CHAT_URL).mock(return_value=chat_response(answer))
        cfg = InsightConfig(prompt="只写一句话", sample_text="上期 CPU 为 88.8%。")

        await generate_summary([make_fact()], "2026-09", cfg, trace=ctx())

        logs = await _all(log_db)
        assert len(logs) == 1
        log = logs[0]
        # 归属信息
        assert (log.kind, log.report_id, log.slot, log.period, log.actor_id) == (
            "generate",
            7,
            "summary",
            "2026-09",
            1,
        )
        # 请求参数
        assert log.model == "test-model"
        assert log.base_url == AI_BASE
        assert log.max_tokens == 600  # min(max(400, 200 * 3), 3000)
        assert log.temperature == cfg.temperature
        assert log.timeout_seconds is not None
        assert log.has_custom is True
        # 真正发出去的 messages：system 红线 + user 段（含写作要求与指标 JSON）
        assert [m["role"] for m in log.messages] == ["system", "user"]
        user_msg = log.messages[1]["content"]
        assert "统计周期：2026-09" in user_msg
        assert "本段写作要求：\n只写一句话" in user_msg
        assert '"name": "cpu_usage"' in user_msg
        # 响应与结果
        assert log.response_text == answer
        assert log.status == "success"
        assert log.error is None
        assert log.retried is False
        assert log.duration_ms is not None

    async def test_no_api_key_records_degraded(
        self, log_db, ai_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "ai_api_key", "")
        await generate_summary([make_fact()], "2026-09", trace=ctx())

        (log,) = await _all(log_db)
        assert log.status == "degraded"
        # 落库文案不含「api_key」字样（否则会被 safe_error 按红线遮成 ***）
        assert log.error == "未配置 AI 密钥"
        # 还没构造请求就降级了
        assert log.messages == []
        assert log.response_text is None

    @respx.mock
    async def test_http_error_records_messages_and_error(
        self, log_db, ai_settings
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
        await generate_summary([make_fact()], "2026-09", trace=ctx())

        (log,) = await _all(log_db)
        assert log.status == "degraded"
        assert log.error is not None
        # 请求确实发出去了，messages 必须留痕（否则查不出「当初送了什么」）
        assert log.messages
        assert log.response_text is None

    @respx.mock
    async def test_guard_retry_records_second_request(self, log_db, ai_settings) -> None:
        """命中范文护栏后的第二次请求（含 assistant 首稿 + 纠正指令）也要留痕。"""
        sample = "上期 CPU 使用率为 88.8%，整体运行平稳，无明显异常波动情况。"
        first = "上期 CPU 使用率为 88.8%，整体运行平稳，无明显异常波动情况。"
        fixed = "本期 CPU 使用率 35%，环比上升 16.67%，整体平稳。"
        route = respx.post(CHAT_URL).mock(
            side_effect=[chat_response(first), chat_response(fixed)]
        )
        cfg = InsightConfig(sample_text=sample)

        await generate_summary([make_fact()], "2026-09", cfg, trace=ctx())

        assert route.call_count == 2
        (log,) = await _all(log_db)
        assert log.retried is True
        assert log.response_text == first
        assert log.retry_response_text == fixed
        assert log.retry_messages is not None
        roles = [m["role"] for m in log.retry_messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert log.retry_messages[2]["content"] == first
        assert "规则 8" in log.retry_messages[3]["content"]
        assert log.retry_error is None

    @respx.mock
    async def test_retry_failure_recorded(self, log_db, ai_settings) -> None:
        """重试请求本身失败：保留首次输出，但把失败原因留痕。"""
        sample = "上期 CPU 使用率为 88.8%，整体运行平稳，无明显异常波动情况。"

        def boom() -> httpx.Response:
            return httpx.Response(500, text="upstream boom")

        # OpenAI SDK 默认对 5xx 自行重试 2 次，每次尝试都会走 respx，
        # 所以 500 要备够，否则 side_effect 耗尽会变成别的异常。
        respx.post(CHAT_URL).mock(
            side_effect=[chat_response(sample), boom(), boom(), boom()]
        )
        cfg = InsightConfig(sample_text=sample)

        await generate_summary([make_fact()], "2026-09", cfg, trace=ctx())

        (log,) = await _all(log_db)
        assert log.retried is True
        assert log.retry_response_text is None
        assert log.retry_error is not None
        # openai SDK 对 5xx 抛 InternalServerError，str(exc) 只有上游返回体、不带状态码
        assert log.retry_error.startswith("InternalServerError")
        assert "upstream boom" in log.retry_error


# --------------------------------------------------------------------------
# 脱敏与清理
# --------------------------------------------------------------------------
class TestRedaction:
    def test_base_url_userinfo_stripped(self) -> None:
        assert (
            ai_trace.sanitize_base_url("https://user:pass@ai.test/v1?k=1")
            == "https://ai.test/v1?k=1"
        )
        assert ai_trace.sanitize_base_url("https://ai.test/v1") == "https://ai.test/v1"
        assert ai_trace.sanitize_base_url("") == ""
        # fragment 不落库
        assert ai_trace.sanitize_base_url("http://ai.test/v1#frag") == "http://ai.test/v1"

    def test_secrets_redacted(self) -> None:
        out = ai_trace.redact("Bearer sk-abcdef123456 rejected")
        assert "sk-abcdef123456" not in out
        assert "Bearer" not in out

        err = ai_trace.safe_error(ValueError("api_key=sk-abcdef123456 无效"))
        assert err is not None
        assert "sk-abcdef123456" not in err
        assert err.startswith("ValueError:")

    def test_error_truncated(self) -> None:
        err = ai_trace.safe_error(RuntimeError("x" * 2000))
        assert err is not None
        assert len(err) == ai_trace.MAX_ERROR_CHARS

    async def test_prune_keeps_recent(self, log_db) -> None:
        async with log_db() as s:
            for _ in range(ai_trace.KEEP_PER_TARGET + 5):
                s.add(AiCallLog(report_id=7, kind="generate", messages=[]))
            await s.commit()

        await ai_trace.save(ctx(), ai_trace.AiCallRecord())
        assert len(await _all(log_db)) == ai_trace.KEEP_PER_TARGET


# --------------------------------------------------------------------------
# 查询接口
# --------------------------------------------------------------------------
class TestAiCallApi:
    async def test_list_filters_by_report(self, api, log_db) -> None:
        async with log_db() as s:
            s.add(
                AiCallLog(
                    report_id=7,
                    kind="generate",
                    slot="summary",
                    model="test-model",
                    messages=[{"role": "system", "content": "红线"}],
                    response_text="hello",
                )
            )
            s.add(AiCallLog(template_id=3, kind="preview", messages=[]))
            await s.commit()

        resp = await api.get("/api/ai-calls", params={"report_id": 7})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        # 列表不含正文（正文可能几 KB，点开再看）
        assert "messages" not in item
        assert (item["kind"], item["slot"], item["model"]) == (
            "generate",
            "summary",
            "test-model",
        )

        detail = await api.get(f"/api/ai-calls/{item['id']}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["messages"][0]["content"] == "红线"
        assert payload["response_text"] == "hello"

    async def test_preview_history_by_template(self, api, log_db) -> None:
        async with log_db() as s:
            s.add(AiCallLog(template_id=3, kind="preview", messages=[]))
            s.add(AiCallLog(template_id=4, kind="preview", messages=[]))
            await s.commit()

        body = (await api.get("/api/ai-calls", params={"template_id": 3})).json()
        assert body["total"] == 1
        body = (await api.get("/api/ai-calls", params={"kind": "preview"})).json()
        assert body["total"] == 2
        body = (await api.get("/api/ai-calls", params={"kind": "generate"})).json()
        assert body["total"] == 0

    async def test_detail_404(self, api) -> None:
        resp = await api.get("/api/ai-calls/999")
        assert resp.status_code == 404

    async def test_requires_auth(self, log_db) -> None:
        app.dependency_overrides.clear()

        async def _get_db():
            async with log_db() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ai-calls")
        app.dependency_overrides.clear()
        assert resp.status_code == 401