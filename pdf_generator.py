"""
PDF & Markdown Generator - WeasyPrint powered.
"""
import re
import markdown
from weasyprint import HTML


def md_to_html(text):
    """Convert Markdown text to HTML using the markdown library."""
    if not text:
        return ""
    # Strip <thinking>...</thinking> blocks — internal agent reasoning
    text = re.sub(r'<thinking>\s*.*?\s*</thinking>', '', text, flags=re.DOTALL)
    return markdown.markdown(text, extensions=['tables', 'sane_lists'])


def _build_metrics_html(snowflake_data):
    """Build an HTML table summarizing marine sales planning metrics for the PDF."""
    if not snowflake_data:
        return ""

    periods = snowflake_data.get('periods') or {}
    py_label = periods.get('prior_year', 'Prior Year')
    ytd_label = periods.get('ytd', 'YTD')

    metrics = snowflake_data.get('customer_metrics') or {}
    py_m = metrics.get('prior_year') or {}
    ytd_m = metrics.get('ytd') or {}

    ports = snowflake_data.get('top5_ports') or {}

    def fmt_num(val, prefix='', suffix=''):
        if val is None:
            return 'N/A'
        try:
            return f"{prefix}{float(val):,.0f}{suffix}"
        except (ValueError, TypeError):
            return str(val)

    def fmt_margin(val):
        if val is None:
            return 'N/A'
        try:
            return f"${float(val):,.2f}/ton"
        except (ValueError, TypeError):
            return 'N/A'

    def win_rate(m):
        try:
            inq = float(m.get('NUM_INQUIRIES') or 0)
            won = float(m.get('NUM_WON') or 0)
            return f"{(won / inq * 100):.1f}%" if inq else 'N/A'
        except (ValueError, TypeError):
            return 'N/A'

    def metric_cell(label, value):
        return f'<td><span class="label">{label}</span><br><span class="value">{value}</span></td>'

    def period_rows(label, m):
        return f"""
      <tr class="section-label"><td colspan="4">{label}</td></tr>
      <tr>
        {metric_cell('Volume (tons)', fmt_num(m.get('VOLUME')))}
        {metric_cell('Gross Profit', fmt_num(m.get('GP'), prefix='$'))}
        {metric_cell('Margin (GP/Ton)', fmt_margin(m.get('MARGIN')))}
        {metric_cell('Win Rate', win_rate(m))}
      </tr>
      <tr>
        {metric_cell('# Won', fmt_num(m.get('NUM_WON')))}
        {metric_cell('# Inquiries', fmt_num(m.get('NUM_INQUIRIES')))}
        {metric_cell('# Lost', fmt_num(m.get('NUM_LOST')))}
        <td></td>
      </tr>
        """

    def port_rows(label, rows):
        header = (
            f'<tr class="section-label"><td colspan="4">Top 5 Ports by Volume &mdash; {label}</td></tr>'
            '<tr>'
            '<td style="text-align:left;font-weight:600;padding-left:12px;">Port</td>'
            '<td style="text-align:left;font-weight:600;padding-left:12px;">Volume (tons)</td>'
            '<td style="text-align:left;font-weight:600;padding-left:12px;">GP (USD)</td>'
            '<td style="text-align:left;font-weight:600;padding-left:12px;">Margin (USD/ton)</td>'
            '</tr>'
        )
        if not rows or not isinstance(rows, list):
            return header + '<tr><td colspan="4" style="color:#999;">No data available</td></tr>'

        def _fmt_margin_cell(v):
            if v is None:
                return 'N/A'
            try:
                return f"{float(v):,.2f}"
            except (ValueError, TypeError):
                return 'N/A'

        body = "".join(
            f'<tr>'
            f'<td style="text-align:left;padding-left:12px;">{r.get("PORT", "N/A")}</td>'
            f'<td style="text-align:left;padding-left:12px;">{fmt_num(r.get("VOLUME"))}</td>'
            f'<td style="text-align:left;padding-left:12px;">{fmt_num(r.get("GP"), prefix="$")}</td>'
            f'<td style="text-align:left;padding-left:12px;">{_fmt_margin_cell(r.get("MARGIN"))}</td>'
            f'</tr>'
            for r in rows
        )
        return header + body

    html = f"""
    <table class="metrics">
      {period_rows(py_label, py_m)}
      {period_rows(ytd_label, ytd_m)}
      {port_rows(py_label, ports.get('prior_year'))}
      {port_rows(ytd_label, ports.get('ytd'))}
    </table>
    """
    return html


def generate_pdf(company_name, agent_results, snowflake_data=None):
    """Generate a styled PDF that mirrors the Markdown brief.

    Args:
        company_name: Customer name for the title.
        agent_results: Dict with keys 'researcher', 'contextualizer', 'strategist'.
        snowflake_data: Optional dict of Snowflake metrics (existing accounts only).
    Returns:
        PDF bytes, or None on failure.
    """
    # --- Build body sections ---
    metrics_html = _build_metrics_html(snowflake_data)

    agent1_html = md_to_html(agent_results.get('researcher', ''))
    agent2_html = md_to_html(agent_results.get('contextualizer', ''))
    agent3_html = md_to_html(agent_results.get('strategist', ''))

    styled_html = f"""
    <html>
    <head>
        <style>
            @page {{ size: A4; margin: 20mm 15mm; }}
            body {{
                font-family: "Helvetica", "Arial", sans-serif;
                font-size: 11px;
                color: #333;
                line-height: 1.55;
                margin: 0;
            }}
            /* ---------- title ---------- */
            .title-block {{
                border-bottom: 3px solid #4169E1;
                padding-bottom: 8px;
                margin-bottom: 18px;
            }}
            .title-block h1 {{
                color: #2c3e50;
                font-size: 22px;
                margin: 0 0 2px 0;
            }}
            .title-block .subtitle {{
                font-size: 13px;
                color: #7f8c8d;
                font-style: italic;
            }}
            /* ---------- metrics table ---------- */
            table.metrics {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 20px;
            }}
            table.metrics td {{
                border: 1px solid #ddd;
                padding: 7px 10px;
                text-align: center;
                width: 25%;
            }}
            table.metrics .section-label td {{
                background: #4169E1;
                color: #fff;
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                text-align: left;
                padding: 5px 10px;
            }}
            table.metrics .label {{
                font-size: 9px;
                color: #777;
                text-transform: uppercase;
                letter-spacing: 0.3px;
            }}
            table.metrics .value {{
                font-size: 14px;
                font-weight: 700;
                color: #2c3e50;
            }}
            /* ---------- agent sections ---------- */
            .agent-header {{
                background: linear-gradient(90deg, #4169E1, #6495ED);
                color: #fff;
                padding: 8px 14px;
                font-size: 14px;
                font-weight: 700;
                border-radius: 4px;
                margin: 22px 0 10px 0;
            }}
            .agent-body {{
                padding: 0 6px 10px 6px;
            }}
            /* ---------- typography ---------- */
            h1 {{ font-size: 18px; color: #2c3e50; margin-top: 18px; }}
            h2 {{ font-size: 15px; color: #e67e22; border-bottom: 1px solid #eee; padding-bottom: 4px; margin-top: 18px; }}
            h3 {{ font-size: 13px; color: #34495e; margin-top: 14px; text-transform: uppercase; }}
            p  {{ margin-bottom: 8px; text-align: justify; }}
            ul, ol {{ padding-left: 22px; margin-bottom: 8px; }}
            li {{ margin-bottom: 4px; }}
            hr {{ border: none; border-top: 1px solid #ccc; margin: 14px 0; }}
            strong {{ color: #000; }}
        </style>
    </head>
    <body>
        <div class="title-block">
            <h1>Summarization of {company_name} by Poseidon</h1>
            <div class="subtitle">a Marine World Application</div>
        </div>

        {metrics_html}

        <div class="agent-header">Agent 1 &mdash; Data Enrichment</div>
        <div class="agent-body">{agent2_html}</div>

        <div class="agent-header">Agent 2 &mdash; Market Research</div>
        <div class="agent-body">{agent1_html}</div>

        <div class="agent-header">Agent 3 &mdash; Salesforce Strategy</div>
        <div class="agent-body">{agent3_html}</div>
    </body>
    </html>
    """

    try:
        return HTML(string=styled_html).write_pdf()
    except Exception:
        return None

