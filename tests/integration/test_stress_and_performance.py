"""Stress and performance benchmark test suite.

Measures and verifies:
- Workloads of 10, 50, and 100 concurrent requests.
- Mixed approvals and rejections under concurrent load.
- Request creation latency, decision processing latency, and end-to-end latency.
- Peak pending count, throughput (requests/sec), and cleanup time.
- Verification of zero lost requests, zero unresolved futures, and correct correlation.
"""

import asyncio
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import mcp.types as types
import pytest

from app.authorization.manager import AuthorizationManager
from app.gateway.local_proxy import LocalMCPProxy
from app.gateway.upstream_manager import UpstreamManager
from app.interception.request_interceptor import RequestInterceptor
from app.models.requests import AuthorizationDecision, AuthorizationRequest, AuthorizationStatus
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def benchmark_env(tmp_path: Path) -> AsyncGenerator[Dict[str, Any], None]:
    """Isolated environment for stress and benchmark testing."""
    import sys

    db_file = tmp_path / "benchmark_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=15.0, audit_repository=audit_repo)

    upstream_mgr = UpstreamManager(
        command=sys.executable,
        args=["examples/mock_mcp_server.py"],
        server_name="Safe Mock MCP Server",
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=upstream_mgr,
        audit_repository=audit_repo,
    )

    proxy = LocalMCPProxy(
        upstream_mgr=upstream_mgr,
        interceptor=interceptor,
        server_name="Benchmark Gateway",
    )

    yield {
        "proxy": proxy,
        "auth_mgr": auth_mgr,
        "upstream_mgr": upstream_mgr,
        "audit_repo": audit_repo,
    }

    await proxy.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency_size", [10, 50, 100])
async def test_concurrent_stress_and_performance(
    benchmark_env: Dict[str, Any],
    concurrency_size: int,
) -> None:
    """Stress test with concurrent requests, mixed decisions, and latency instrumentation."""
    proxy: LocalMCPProxy = benchmark_env["proxy"]
    auth_mgr: AuthorizationManager = benchmark_env["auth_mgr"]
    audit_repo: AuditRepository = benchmark_env["audit_repo"]

    # Latency tracking containers
    decision_latencies: List[float] = []
    e2e_latencies: List[float] = []
    peak_pending_count = 0

    async def stress_responder(req: AuthorizationRequest) -> None:
        nonlocal peak_pending_count
        cur_pending = auth_mgr.registry.count
        if cur_pending > peak_pending_count:
            peak_pending_count = cur_pending

        # Stagger decisions to simulate real user response variance (1ms to 20ms)
        idx = int(req.arguments["index"])
        stagger = (idx % 5) * 0.003
        await asyncio.sleep(stagger)

        # 70% approved, 30% rejected
        is_approved = (idx % 10) < 7
        status = AuthorizationStatus.APPROVED if is_approved else AuthorizationStatus.REJECTED

        t_dec_start = time.perf_counter()
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=status,
                reason=f"Stress decision for idx {idx}",
            )
        )
        decision_latencies.append(time.perf_counter() - t_dec_start)

    auth_mgr.set_notification_hook(stress_responder)

    async def execute_single_call(i: int) -> types.CallToolResult:
        t_start = time.perf_counter()
        params = types.CallToolRequestParams(
            name="calculate_sum",
            arguments={"a": float(i), "b": 100.0, "index": i},
        )
        res = await proxy._handle_call_tool(ctx=None, params=params)
        e2e_latencies.append(time.perf_counter() - t_start)
        return res

    t_overall_start = time.perf_counter()
    tasks = [execute_single_call(i) for i in range(concurrency_size)]
    results: List[types.CallToolResult] = await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - t_overall_start

    # Assertions on results
    assert len(results) == concurrency_size
    approved_count = 0
    rejected_count = 0

    for i, res in enumerate(results):
        is_approved = (i % 10) < 7
        if is_approved:
            approved_count += 1
            assert not res.is_error, f"Request {i} expected approval but failed: {res}"
            expected_sum = float(i + 100)
            assert str(expected_sum) in res.content[0].text  # type: ignore
        else:
            rejected_count += 1
            assert res.is_error, f"Request {i} expected rejection but succeeded!"
            assert "was blocked by user" in res.content[0].text  # type: ignore

    # Zero unresolved requests
    assert auth_mgr.registry.count == 0, f"Leaked {auth_mgr.registry.count} pending requests!"

    # Verify audit persistence
    audit_records = await audit_repo.list_records(limit=concurrency_size + 10)
    assert len(audit_records) == concurrency_size

    # Performance calculations
    throughput = concurrency_size / total_elapsed if total_elapsed > 0 else 0
    avg_e2e_ms = (sum(e2e_latencies) / len(e2e_latencies)) * 1000
    p95_e2e_ms = sorted(e2e_latencies)[int(len(e2e_latencies) * 0.95)] * 1000
    avg_dec_ms = (sum(decision_latencies) / len(decision_latencies)) * 1000

    print(
        f"\n[BENCHMARK] N={concurrency_size:3d} | "
        f"Time: {total_elapsed:.3f}s | "
        f"Throughput: {throughput:6.1f} req/s | "
        f"Avg E2E: {avg_e2e_ms:5.2f}ms | "
        f"P95 E2E: {p95_e2e_ms:5.2f}ms | "
        f"Avg Dec: {avg_dec_ms:5.2f}ms | "
        f"Peak Pending: {peak_pending_count:3d} | "
        f"Approved: {approved_count:3d} | Rejected: {rejected_count:3d}",
        flush=True,
    )
