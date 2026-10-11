import json
import logging
import tempfile
from decimal import Decimal

import numpy as np
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend (no display needed)
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from langchain_core.tools import tool
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")

# Consistent color palette for Cashy charts
COLORS = [
    "#2196F3",  # blue
    "#4CAF50",  # green
    "#FF9800",  # orange
    "#E91E63",  # pink
    "#9C27B0",  # purple
    "#00BCD4",  # cyan
    "#FFC107",  # amber
    "#607D8B",  # blue-grey
    "#F44336",  # red
    "#8BC34A",  # light green
    "#3F51B5",  # indigo
    "#795548",  # brown
]

VALID_CHART_TYPES = ("bar", "horizontal_bar", "pie", "line")


def _format_currency(x, _pos):
    """Format axis values as $X,XXX."""
    return f"${x:,.0f}"


def _to_float(val):
    """Convert Decimal or other numeric types to float for matplotlib."""
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


@tool
def generate_chart(
    chart_type: str,
    title: str,
    sql_query: str,
    x_column: str,
    y_column: str,
    y2_column: str = "",
    x_label: str = "",
    y_label: str = "",
) -> str:
    """Generate a chart from SQL query results and return the image path.

    Args:
        chart_type: Type of chart - "bar", "horizontal_bar", "pie", or "line"
        title: Chart title displayed at the top
        sql_query: SELECT query to fetch the chart data
        x_column: Column name for x-axis (categories/labels)
        y_column: Column name for y-axis (first series of values)
        y2_column: Optional second column for comparison charts (e.g., budget vs actual). Creates grouped bars or a second line.
        x_label: Optional label for x-axis
        y_label: Optional label for y-axis
    """
    logger.info("[generate_chart] type=%s, title=%s", chart_type, title)
    logger.info("[generate_chart] SQL: %s", sql_query[:120])

    # Validate chart type
    if chart_type not in VALID_CHART_TYPES:
        return json.dumps({
            "success": False,
            "error": f"Invalid chart_type '{chart_type}'. Must be one of: {', '.join(VALID_CHART_TYPES)}",
        })

    # Validate SQL is SELECT-only
    if not sql_query.strip().upper().startswith("SELECT"):
        logger.warning("[generate_chart] Rejected non-SELECT query")
        return json.dumps({"success": False, "error": "Only SELECT queries allowed"})

    try:
        # Execute query
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_query)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        if not rows:
            return json.dumps({"success": False, "error": "Query returned no data"})

        # Validate column names exist in results
        for col_name, col_label in [(x_column, "x_column"), (y_column, "y_column")]:
            if col_name not in columns:
                return json.dumps({
                    "success": False,
                    "error": f"{col_label} '{col_name}' not found. Available: {columns}",
                })

        has_y2 = bool(y2_column)
        if has_y2 and y2_column not in columns:
            return json.dumps({
                "success": False,
                "error": f"y2_column '{y2_column}' not found. Available: {columns}",
            })

        x_idx = columns.index(x_column)
        y_idx = columns.index(y_column)

        labels = [str(row[x_idx]) for row in rows]
        values = [_to_float(row[y_idx]) for row in rows]

        values2 = None
        if has_y2:
            y2_idx = columns.index(y2_column)
            values2 = [_to_float(row[y2_idx]) for row in rows]

        logger.info("[generate_chart] %d data points, y2=%s", len(labels), has_y2)

        # Generate chart
        fig, ax = plt.subplots(figsize=(10, 6))
        colors = COLORS[: len(labels)]

        if chart_type == "bar":
            if has_y2:
                # Grouped bar chart
                x_pos = np.arange(len(labels))
                width = 0.35
                ax.bar(x_pos - width / 2, values, width, label=y_column.replace("_", " ").title(), color=COLORS[0])
                ax.bar(x_pos + width / 2, values2, width, label=y2_column.replace("_", " ").title(), color=COLORS[1])
                ax.set_xticks(x_pos)
                ax.set_xticklabels(labels, rotation=45, ha="right")
                ax.legend()
            else:
                ax.bar(labels, values, color=colors)
                plt.xticks(rotation=45, ha="right")
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(_format_currency))
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)

        elif chart_type == "horizontal_bar":
            if has_y2:
                y_pos = np.arange(len(labels))
                height = 0.35
                ax.barh(y_pos - height / 2, values, height, label=y_column.replace("_", " ").title(), color=COLORS[0])
                ax.barh(y_pos + height / 2, values2, height, label=y2_column.replace("_", " ").title(), color=COLORS[1])
                ax.set_yticks(y_pos)
                ax.set_yticklabels(labels)
                ax.legend()
            else:
                ax.barh(labels, values, color=colors)
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(_format_currency))
            if x_label:
                ax.set_ylabel(x_label)  # Swapped for horizontal
            if y_label:
                ax.set_xlabel(y_label)

        elif chart_type == "pie":
            ax.pie(
                values,
                labels=labels,
                colors=colors,
                autopct="%1.1f%%",
                startangle=90,
            )
            ax.axis("equal")

        elif chart_type == "line":
            ax.plot(labels, values, color=COLORS[0], marker="o", linewidth=2, label=y_column.replace("_", " ").title() if has_y2 else None)
            if has_y2:
                ax.plot(labels, values2, color=COLORS[1], marker="s", linewidth=2, label=y2_column.replace("_", " ").title())
                ax.legend()
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(_format_currency))
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)
            plt.xticks(rotation=45, ha="right")

        ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
        fig.tight_layout()

        # Save to temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".png", prefix="cashy_chart_", delete=False)
        fig.savefig(tmp.name, dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("[generate_chart] Saved chart to %s", tmp.name)

        summary = f"{chart_type.replace('_', ' ').title()} chart with {len(labels)} data points"
        if has_y2:
            summary += f" comparing {y_column} vs {y2_column}"

        return json.dumps({
            "success": True,
            "chart_path": tmp.name,
            "chart_type": chart_type,
            "data_points": len(labels),
            "summary": summary,
        })

    except Exception as e:
        logger.error("[generate_chart] Error: %s", e)
        plt.close("all")
        return json.dumps({"success": False, "error": str(e)})
