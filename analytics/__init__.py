"""
analytics/ — Analytics + monitoring (Phase 8 Godmode).

Public API:
    from analytics.collector import MetricsCollector, get_collector, MetricsSnapshot
    from analytics.dashboard import DashboardBuilder, send_dashboard, send_daily_report
    from analytics.alerts import AlertManager, Alert, AlertLevel
    from analytics.tasks import register_tasks, get_beat_schedule
"""
