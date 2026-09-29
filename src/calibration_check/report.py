"""Static Plotly HTML calibration report."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from .analysis import CalibrationRun, SignalComparison


def export_html(run: CalibrationRun, output_path: str | Path | None = None) -> Path:
    """Write a standalone, server-free calibration report."""
    if output_path is None:
        output_path = (
            run.config.project.output_folder / run.config.report.output_file
        )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    include_plotly = True if run.config.report.self_contained else "cdn"
    sections: list[str] = []
    for index, comparison in enumerate(run.comparisons):
        sections.append(_comparison_html(
            comparison,
            include_plotlyjs=include_plotly if index == 0 else False,
        ))
    target.write_text(
        _page_html(run, "\n".join(sections)),
        encoding="utf-8",
    )
    return target


def _comparison_html(
        comparison: SignalComparison,
        include_plotlyjs: bool | str,
        ) -> str:
    figure = go.Figure()
    if comparison.observed is not None:
        figure.add_trace(go.Scatter(
            x=comparison.observed.index,
            y=comparison.observed.values,
            name="Observed",
            mode="lines",
            line={"color": "#111827", "width": 2},
        ))
    if comparison.simulated is not None:
        figure.add_trace(go.Scatter(
            x=comparison.simulated.index,
            y=comparison.simulated.values,
            name="Simulated",
            mode="lines",
            line={"color": "#1677b8", "width": 2},
        ))
    figure.update_layout(
        height=380,
        margin={"l": 56, "r": 20, "t": 24, "b": 44},
        legend={"orientation": "h", "y": 1.08},
        hovermode="x unified",
        template="plotly_white",
    )
    graph = figure.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        config={"responsive": True, "displaylogo": False},
    )
    rows = "".join(
        f"<tr><th>{html.escape(_metric_label(name))}</th>"
        f"<td>{_format_value(value)}</td></tr>"
        for name, value in comparison.metrics.items()
    ) or '<tr><td colspan="2">No statistics available</td></tr>'
    warnings = "".join(
        f"<li>{html.escape(message)}</li>" for message in comparison.warnings
    )
    return f"""
    <section class="comparison" data-gauge="{html.escape(comparison.gauge_alias)}"
             data-event="{html.escape(comparison.event_name)}">
      <div class="section-heading">
        <div>
          <h2>{html.escape(comparison.signal_alias)}</h2>
          <p>{html.escape(comparison.gauge_alias)} · {html.escape(comparison.event_name)} · {html.escape(comparison.result_name)}</p>
        </div>
      </div>
      <div class="content">
        <table><tbody>{rows}</tbody></table>
        <div class="plot">{graph}</div>
      </div>
      {f'<ul class="warnings">{warnings}</ul>' if warnings else ''}
    </section>
    """


def _page_html(run: CalibrationRun, sections: str) -> str:
    title = html.escape(run.config.report.title)
    gauge_options = "".join(
        f'<option value="{html.escape(gauge.alias)}">{html.escape(gauge.alias)}</option>'
        for gauge in run.config.gauges
    )
    event_names = sorted({
        event.name for gauge in run.config.gauges for event in gauge.events
    })
    event_options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in event_names
    )
    run_warnings = "".join(
        f"<li>{html.escape(message)}</li>" for message in dict.fromkeys(run.warnings)
    )
    empty = '<p class="empty">No comparisons were generated.</p>' if not sections else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; font-family: Arial, Helvetica, sans-serif; }}
    body {{ margin: 0; color: #17212b; background: #eef2f5; }}
    header {{ position: sticky; top: 0; z-index: 5; display: flex; gap: 24px;
      align-items: center; justify-content: space-between; padding: 14px 24px;
      border-bottom: 1px solid #c9d2da; background: #fff; }}
    h1 {{ margin: 0; font-size: 21px; }}
    h2 {{ margin: 0; font-size: 18px; }}
    p {{ margin: 4px 0 0; color: #52606d; }}
    label {{ font-size: 13px; font-weight: 700; }}
    select {{ min-width: 220px; margin-left: 8px; padding: 7px 9px;
      border: 1px solid #9aa8b5; border-radius: 4px; background: #fff; }}
    main {{ max-width: 1500px; margin: 0 auto; padding: 20px; }}
    .comparison {{ margin-bottom: 16px; border: 1px solid #cbd5df;
      border-radius: 6px; background: #fff; }}
    .section-heading {{ padding: 14px 16px 0; }}
    .filters {{ display: flex; gap: 16px; align-items: center; }}
    .content {{ padding: 8px 14px 14px; }}
    .plot {{ min-width: 0; }}
    table {{ width: 100%; margin: 6px 0 10px; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px; border-bottom: 1px solid #dde3e8; text-align: right; }}
    th {{ text-align: left; font-weight: 600; }}
    .warnings {{ margin: 0; padding: 0 32px 14px; color: #8a3b12; }}
    .run-warnings {{ margin: 0 0 16px; padding: 12px 28px; border: 1px solid #e3b341;
      border-radius: 6px; background: #fff8dc; }}
    .empty {{ padding: 18px; border: 1px solid #cbd5df; background: #fff; }}
    @media (max-width: 850px) {{
      header {{ align-items: stretch; flex-direction: column; gap: 10px; }}
      .filters {{ align-items: stretch; flex-direction: column; gap: 8px; }}
      select {{ width: 100%; margin: 6px 0 0; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="filters">
      <label>Gauge<select id="gauge-filter"><option value="">All gauges</option>{gauge_options}</select></label>
      <label>Event<select id="event-filter"><option value="">All events</option>{event_options}</select></label>
    </div>
  </header>
  <main>
    {f'<ul class="run-warnings">{run_warnings}</ul>' if run_warnings else ''}
    {sections}{empty}
  </main>
  <script>
    const gaugeFilter = document.getElementById("gauge-filter");
    const eventFilter = document.getElementById("event-filter");
    const sections = Array.from(document.querySelectorAll(".comparison"));
    function applyFilters() {{
      sections.forEach(section => {{
        const gaugeMismatch = gaugeFilter.value && section.dataset.gauge !== gaugeFilter.value;
        const eventMismatch = eventFilter.value && section.dataset.event !== eventFilter.value;
        section.hidden = gaugeMismatch || eventMismatch;
        if (!section.hidden) {{
          const plot = section.querySelector(".plotly-graph-div");
          if (plot && window.Plotly) Plotly.Plots.resize(plot);
        }}
      }});
    }}
    gaugeFilter.addEventListener("change", applyFilters);
    eventFilter.addEventListener("change", applyFilters);
  </script>
</body>
</html>"""


def _metric_label(name: str) -> str:
    return name.replace("_", " ").title()


def _format_value(value: float) -> str:
    if pd.isna(value):
        return "—"
    return f"{value:,.4g}"
