"""Build Plotly figure JSON for the web UI (Plotly.js)."""

from collections import Counter
from typing import Any


def _is_numeric(val: Any) -> bool:
    try:
        float(str(val).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def _pick_columns(results: list[dict]) -> tuple[str, str | None, list[str], list[float]]:
    keys = list(results[0].keys())
    label_col = next((k for k in keys if not _is_numeric(results[0].get(k, ""))), keys[0])
    value_col = next(
        (k for k in keys if k != label_col and _is_numeric(results[0].get(k, ""))),
        None,
    )
    if not value_col:
        counts = Counter(str(r.get(label_col, "")) for r in results)
        labels = list(counts.keys())[:20]
        values = [float(counts[l]) for l in labels]
        return label_col, "count", labels, values

    labels = [str(r.get(label_col, ""))[:40] for r in results[:50]]
    values = []
    for r in results[:50]:
        try:
            values.append(float(str(r.get(value_col, 0) or 0).replace(",", "")))
        except ValueError:
            values.append(0.0)
    return label_col, value_col, labels, values


def build_chart_payload(results: list[dict], chart_type: str | None) -> dict[str, Any] | None:
    """
    Response for /chart-figure: either a Plotly payload or a stat card descriptor.
    """
    if not results:
        return None

    if len(results) == 1 and len(results[0]) == 1:
        k = list(results[0].keys())[0]
        v = results[0][k]
        return {"kind": "stat", "stat_key": k, "stat_value": v}

    effective = chart_type if chart_type in ("bar", "line", "pie") else "bar"
    label_col, value_col, labels, values = _pick_columns(results)
    if not labels:
        return None

    if effective == "pie" and len(labels) > 8:
        effective = "bar"

    layout: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "#0d0d14",
        "font": {"family": "Space Mono, monospace", "color": "#e8e8f0", "size": 11},
        "margin": {"l": 48, "r": 24, "t": 24, "b": 88},
        "xaxis": {
            "gridcolor": "#2a2a38",
            "tickangle": -30,
            "tickfont": {"size": 10, "color": "#6b6b80"},
        },
        "yaxis": {"gridcolor": "#2a2a38", "tickfont": {"size": 10, "color": "#6b6b80"}},
        "showlegend": effective == "pie",
    }

    trace_color = "#7c6aff"
    if effective == "pie":
        palette = ["#7c6aff", "#ff6a9d", "#6affb8", "#f4b942", "#06b6d4", "#ef4444", "#8b5cf6", "#10b981"]
        data = [
            {
                "type": "pie",
                "labels": labels,
                "values": values,
                "marker": {"colors": [palette[i % len(palette)] for i in range(len(labels))]},
                "textinfo": "percent+label",
                "textfont": {"size": 10},
                "hole": 0.33,
            }
        ]
    elif effective == "line":
        data = [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "x": labels,
                "y": values,
                "line": {"color": trace_color, "width": 2},
                "marker": {"color": trace_color, "size": 7},
                "name": value_col or "value",
            }
        ]
    else:
        data = [
            {
                "type": "bar",
                "x": labels,
                "y": values,
                "marker": {"color": trace_color},
                "name": value_col or "value",
            }
        ]

    return {
        "kind": "plotly",
        "chart_type": effective,
        "label_col": label_col,
        "value_col": value_col,
        "plotly": {"data": data, "layout": layout},
    }
