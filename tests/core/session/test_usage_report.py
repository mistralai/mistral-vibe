from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from vibe.core.session.usage_report import (
    UNPINNED_MODEL_LABEL,
    UsageBucket,
    build_usage_report,
    format_usage_csv,
    format_usage_markdown,
    generate_usage_insight,
    parse_usage_args,
    parse_usage_window,
)


def _write_meta(
    root: Path,
    *,
    name: str,
    start_time: str,
    model: str,
    cost: float,
    prompt: int,
    completion: int,
    cached: int = 0,
    input_price: float = 1.0,
    output_price: float = 2.0,
) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    meta = session_dir / "meta.json"
    meta.write_text(
        (
            "{\n"
            f'  "session_id": "{name}",\n'
            f'  "start_time": "{start_time}",\n'
            f'  "config": {{"active_model": "{model}"}},\n'
            '  "stats": {\n'
            f'    "session_prompt_tokens": {prompt},\n'
            f'    "session_completion_tokens": {completion},\n'
            f'    "session_cached_tokens": {cached},\n'
            f'    "session_cost": {cost},\n'
            f'    "input_price_per_million": {input_price},\n'
            f'    "output_price_per_million": {output_price}\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    return meta


class TestParseUsageWindow:
    def test_all_and_empty(self) -> None:
        assert parse_usage_window("all") is None
        assert parse_usage_window("") is None
        assert parse_usage_window("  ALL  ") is None

    def test_day_windows(self) -> None:
        today = date(2026, 8, 22)
        assert parse_usage_window("7d", today=today) == date(2026, 8, 16)
        assert parse_usage_window("30d", today=today) == today - timedelta(days=29)
        assert parse_usage_window("1d", today=today) == today

    def test_invalid_window(self) -> None:
        with pytest.raises(ValueError, match="Unknown usage window"):
            parse_usage_window("week")
        with pytest.raises(ValueError, match="positive"):
            parse_usage_window("0d")


class TestParseUsageArgs:
    def test_window_and_csv(self, tmp_path: Path) -> None:
        today = date(2026, 8, 22)
        parsed = parse_usage_args(
            f"7d --csv {tmp_path / 'out.csv'} --insight", today=today
        )
        assert parsed.error is None
        assert parsed.since == date(2026, 8, 16)
        assert parsed.csv_path == tmp_path / "out.csv"
        assert parsed.insight is True

    def test_csv_requires_path(self) -> None:
        parsed = parse_usage_args("--csv")
        assert parsed.error is not None
        assert "requires a file path" in parsed.error

    def test_unknown_flag(self) -> None:
        parsed = parse_usage_args("--json")
        assert parsed.error is not None
        assert "Unknown flag" in parsed.error


class TestBuildUsageReport:
    def test_aggregates_by_day_and_model(self, tmp_path: Path) -> None:
        _write_meta(
            tmp_path,
            name="session_20260820_aaa",
            start_time="2026-08-20T10:00:00+00:00",
            model="devstral-2",
            cost=0.10,
            prompt=1000,
            completion=200,
        )
        _write_meta(
            tmp_path,
            name="session_20260820_bbb",
            start_time="2026-08-20T18:00:00+00:00",
            model="mistral-medium-3.5",
            cost=0.25,
            prompt=2000,
            completion=400,
            cached=100,
        )
        _write_meta(
            tmp_path,
            name="session_20260821_ccc",
            start_time="2026-08-21T09:00:00+00:00",
            model="devstral-2",
            cost=0.05,
            prompt=500,
            completion=50,
        )

        report = build_usage_report(tmp_path)
        assert report.session_count == 3
        assert report.total.cost == pytest.approx(0.40)
        assert report.total.prompt_tokens == 3500
        assert report.total.completion_tokens == 650
        assert report.total.cached_tokens == 100
        assert report.by_day["2026-08-20"].sessions == 2
        assert report.by_day["2026-08-20"].cost == pytest.approx(0.35)
        assert report.by_model["devstral-2"].sessions == 2
        assert report.by_model["devstral-2"].cost == pytest.approx(0.15)
        assert report.by_model["mistral-medium-3.5"].cost == pytest.approx(0.25)

    def test_date_filter(self, tmp_path: Path) -> None:
        _write_meta(
            tmp_path,
            name="session_old",
            start_time="2026-08-01T00:00:00+00:00",
            model="devstral-2",
            cost=1.0,
            prompt=10,
            completion=1,
        )
        _write_meta(
            tmp_path,
            name="session_new",
            start_time="2026-08-20T00:00:00+00:00",
            model="devstral-2",
            cost=0.2,
            prompt=20,
            completion=2,
        )
        report = build_usage_report(tmp_path, since=date(2026, 8, 15))
        assert report.session_count == 1
        assert report.total.cost == pytest.approx(0.2)
        assert "2026-08-01" not in report.by_day

    def test_unpinned_model_label(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session_unpinned"
        session_dir.mkdir()
        (session_dir / "meta.json").write_text(
            (
                "{\n"
                '  "session_id": "unpinned",\n'
                '  "start_time": "2026-08-22T12:00:00+00:00",\n'
                '  "config": {"active_model": ""},\n'
                '  "stats": {"session_prompt_tokens": 10, "session_completion_tokens": 1,'
                ' "session_cost": 0.01}\n'
                "}\n"
            ),
            encoding="utf-8",
        )
        report = build_usage_report(tmp_path)
        assert UNPINNED_MODEL_LABEL in report.by_model
        assert report.by_model[UNPINNED_MODEL_LABEL].sessions == 1

    def test_skips_corrupt_meta(self, tmp_path: Path) -> None:
        _write_meta(
            tmp_path,
            name="session_ok",
            start_time="2026-08-22T00:00:00+00:00",
            model="devstral-2",
            cost=0.01,
            prompt=5,
            completion=1,
        )
        bad = tmp_path / "session_bad"
        bad.mkdir()
        (bad / "meta.json").write_text("{not-json", encoding="utf-8")
        report = build_usage_report(tmp_path)
        assert report.session_count == 1
        assert report.skipped_files == 1

    def test_recomputes_cost_when_missing(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session_recompute"
        session_dir.mkdir()
        (session_dir / "meta.json").write_text(
            (
                "{\n"
                '  "session_id": "recompute",\n'
                '  "start_time": "2026-08-22T00:00:00+00:00",\n'
                '  "config": {"active_model": "devstral-2"},\n'
                '  "stats": {\n'
                '    "session_prompt_tokens": 1000000,\n'
                '    "session_completion_tokens": 500000,\n'
                '    "session_cached_tokens": 0,\n'
                '    "input_price_per_million": 1.0,\n'
                '    "output_price_per_million": 2.0\n'
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        report = build_usage_report(tmp_path)
        # 1M * $1 + 0.5M * $2 = $2
        assert report.total.cost == pytest.approx(2.0)

    def test_empty_dir(self, tmp_path: Path) -> None:
        report = build_usage_report(tmp_path / "missing")
        assert report.session_count == 0
        assert report.total == UsageBucket()


class TestFormatters:
    def test_markdown_empty(self) -> None:
        text = format_usage_markdown(build_usage_report(Path("/no/such/dir")))
        assert "No saved sessions" in text

    def test_markdown_tables_and_bars(self, tmp_path: Path) -> None:
        _write_meta(
            tmp_path,
            name="session_a",
            start_time="2026-08-20T00:00:00+00:00",
            model="devstral-2",
            cost=0.10,
            prompt=100,
            completion=10,
        )
        _write_meta(
            tmp_path,
            name="session_b",
            start_time="2026-08-21T00:00:00+00:00",
            model="mistral-medium-3.5",
            cost=0.40,
            prompt=400,
            completion=40,
        )
        report = build_usage_report(tmp_path, since=date(2026, 8, 1))
        text = format_usage_markdown(report, insight="Spend spiked on medium.")
        assert "### By day" in text
        assert "### By model" in text
        assert "devstral-2" in text
        assert "▁" in text or "█" in text or "▇" in text
        assert "Spend spiked on medium." in text
        assert "since 2026-08-01" in text

    def test_csv_export(self, tmp_path: Path) -> None:
        _write_meta(
            tmp_path,
            name="session_a",
            start_time="2026-08-20T00:00:00+00:00",
            model="devstral-2",
            cost=0.10,
            prompt=100,
            completion=10,
        )
        csv_text = format_usage_csv(build_usage_report(tmp_path))
        assert "dimension,key,cost_usd" in csv_text
        assert "day,2026-08-20" in csv_text
        assert "model,devstral-2" in csv_text
        assert "total,all" in csv_text


class TestGenerateUsageInsight:
    def test_skips_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        report = build_usage_report(Path("/no/such"))
        assert generate_usage_insight(report) is None
