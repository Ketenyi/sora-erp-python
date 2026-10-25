"""Vector PDF exports: Gantt charts, MOM documents and analytics reports."""

from __future__ import annotations

import html
import io
from datetime import date, timedelta

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen import canvas

STATUS_COLORS = {
    "backlog": colors.HexColor("#94a3b8"),
    "in_progress": colors.HexColor("#3b82f6"),
    "blocked": colors.HexColor("#ef4444"),
    "in_qa": colors.HexColor("#f59e0b"),
    "complete": colors.HexColor("#10b981"),
}

STATUS_LABELS = {
    "backlog": "Backlog",
    "in_progress": "In Progress",
    "blocked": "Blocked",
    "in_qa": "In QA",
    "complete": "Complete",
}

GOLD = colors.HexColor("#c9a227")
NAVY = colors.HexColor("#1e293b")
SLATE = colors.HexColor("#64748b")
LIGHT = colors.HexColor("#e2e8f0")
PAPER = colors.HexColor("#f8fafc")


def _task_dates(task, project):
    start = task.start_date or project.start_date or project.baseline_start_date
    end = task.end_date or task.deadline_date or project.end_date or project.baseline_end_date
    if not start and not end:
        today = timezone.localdate()
        start, end = today, today + timedelta(days=7)
    if not start:
        start = end - timedelta(days=1)
    if not end:
        end = start + timedelta(days=1)
    if end < start:
        start, end = end, start
    return start, end


def gantt_pdf(project, tasks) -> bytes:
    buffer = io.BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    pdf.setTitle(f"Gantt - {project.project_code}")
    pdf.setAuthor("SORA ERP")

    rows = []
    for task in tasks:
        start, end = _task_dates(task, project)
        rows.append({"task": task, "start": start, "end": end})

    all_starts = [row["start"] for row in rows] + [
        value for value in (project.start_date, project.baseline_start_date) if value
    ]
    all_ends = [row["end"] for row in rows] + [
        value for value in (project.end_date, project.baseline_end_date) if value
    ]
    if not all_starts or not all_ends:
        today = timezone.localdate()
        all_starts, all_ends = [today], [today + timedelta(days=30)]
    range_start = min(all_starts) - timedelta(days=3)
    range_end = max(all_ends) + timedelta(days=3)
    if range_end <= range_start:
        range_end = range_start + timedelta(days=30)
    total_days = (range_end - range_start).days + 1

    left_margin = 16 * mm
    right_margin = 12 * mm
    top_margin = 14 * mm
    label_width = 72 * mm
    timeline_width = page_width - left_margin - right_margin - label_width
    day_width = timeline_width / total_days
    row_height = 15
    header_height = 104
    footer_height = 32
    usable_height = page_height - top_margin - header_height - footer_height
    rows_per_page = max(int(usable_height / row_height), 5)
    pages = [rows[index:index + rows_per_page] for index in range(0, len(rows), rows_per_page)] or [[]]

    def draw_header(page_number, total_pages):
        pdf.setFillColor(NAVY)
        pdf.rect(0, page_height - 10 * mm, page_width, 10 * mm, stroke=0, fill=1)
        pdf.setFillColor(GOLD)
        pdf.rect(0, page_height - 10 * mm, 60 * mm, 10 * mm, stroke=0, fill=1)
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left_margin, page_height - 6.8 * mm, "SORA ERP · PROJECT MANAGEMENT")
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(left_margin, page_height - 22 * mm, project.project_name[:80])
        pdf.setFillColor(SLATE)
        pdf.setFont("Helvetica", 8.5)
        status = project.get_status_display()
        baseline = (
            f"Baseline locked {timezone.localtime(project.baseline_locked_at):%d %b %Y}"
            if project.baseline_locked_at
            else "Baseline not locked"
        )
        pdf.drawString(
            left_margin,
            page_height - 28 * mm,
            f"{project.project_code}  ·  {status}  ·  {baseline}  ·  Gantt chart generated {timezone.localtime():%d %b %Y %H:%M}",
        )

        chart_top = page_height - header_height
        pdf.setFillColor(NAVY)
        pdf.rect(left_margin, chart_top - 18, label_width + timeline_width, 18, stroke=0, fill=1)
        pdf.setFont("Helvetica-Bold", 7.5)
        pdf.setFillColor(colors.white)
        pdf.drawString(left_margin + 4, chart_top - 12, "TASK")
        pdf.drawString(left_margin + label_width + 4, chart_top - 12, "TIMELINE")

        # Month grid + labels
        cursor = date(range_start.year, range_start.month, 1)
        while cursor <= range_end:
            if cursor < range_start:
                cursor_x = left_margin + label_width
            else:
                cursor_x = left_margin + label_width + (cursor - range_start).days * day_width
            if cursor_x >= left_margin + label_width:
                pdf.setStrokeColor(LIGHT)
                pdf.setLineWidth(0.35)
                pdf.line(cursor_x, chart_top - 18, cursor_x, chart_top - 18 - usable_height)
                pdf.setFillColor(SLATE)
                pdf.setFont("Helvetica", 6.5)
                pdf.drawString(cursor_x + 2, chart_top - 15, cursor.strftime("%b %y"))
            next_month = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
            cursor = next_month

        # Baseline band
        if project.baseline_start_date and project.baseline_end_date:
            start_x = left_margin + label_width + max((project.baseline_start_date - range_start).days, 0) * day_width
            width = max(
                ((project.baseline_end_date - project.baseline_start_date).days + 1) * day_width,
                day_width,
            )
            pdf.setFillColor(colors.Color(0.79, 0.64, 0.15, alpha=0.08))
            pdf.rect(start_x, chart_top - 18 - usable_height, width, usable_height, stroke=0, fill=1)
            pdf.setFillColor(GOLD)
            pdf.setFont("Helvetica", 6)
            pdf.drawString(start_x + 2, chart_top - 26, "BASELINE")

        today = timezone.localdate()
        if range_start <= today <= range_end:
            today_x = left_margin + label_width + (today - range_start).days * day_width
            pdf.setStrokeColor(colors.HexColor("#ef4444"))
            pdf.setDash(2, 2)
            pdf.setLineWidth(0.6)
            pdf.line(today_x, chart_top - 18, today_x, chart_top - 18 - usable_height)
            pdf.setDash()
            pdf.setFillColor(colors.HexColor("#ef4444"))
            pdf.setFont("Helvetica", 6)
            pdf.drawString(today_x + 2, chart_top - 18 - usable_height + 2, "TODAY")

        pdf.setFillColor(SLATE)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(
            left_margin,
            16,
            f"Page {page_number} of {total_pages}  ·  {len(rows)} task(s)  ·  Bars: current plan  ·  Shaded band: locked baseline",
        )
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawRightString(page_width - right_margin, 16, "SORA ERP")

    def draw_legend():
        x = page_width - right_margin - 170
        y = page_height - 26 * mm
        pdf.setFont("Helvetica", 6.5)
        for key, color in STATUS_COLORS.items():
            pdf.setFillColor(color)
            pdf.roundRect(x, y - 1, 10, 5, 1.5, stroke=0, fill=1)
            pdf.setFillColor(SLATE)
            pdf.drawString(x + 13, y, STATUS_LABELS[key])
            x += 45

    total_pages = len(pages)
    for page_number, page_rows in enumerate(pages, start=1):
        draw_header(page_number, total_pages)
        draw_legend()
        chart_top = page_height - header_height
        y = chart_top - 18
        for row in page_rows:
            y -= row_height
            pdf.setStrokeColor(colors.HexColor("#eef2f6"))
            pdf.setLineWidth(0.4)
            pdf.line(left_margin, y, page_width - right_margin, y)
            task = row["task"]
            pdf.setFillColor(NAVY)
            pdf.setFont("Helvetica-Bold", 7.2)
            name = (task.task_name or "")[:46]
            pdf.drawString(left_margin + 4, y + 4.5, name)
            pdf.setFillColor(SLATE)
            pdf.setFont("Helvetica", 5.8)
            assignee = f"Task #{task.id} · {task.get_status_display()} · {task.completion_percent or 0:.0f}%"
            pdf.drawString(left_margin + 4, y + 0.2, assignee[:56])

            start_x = left_margin + label_width + (row["start"] - range_start).days * day_width
            width = max(((row["end"] - row["start"]).days + 1) * day_width, 3)
            pdf.setFillColor(STATUS_COLORS.get(task.status, SLATE))
            pdf.roundRect(start_x, y + 3.5, width, 7, 2, stroke=0, fill=1)
            if task.deadline_date:
                due_x = left_margin + label_width + (task.deadline_date - range_start).days * day_width
                pdf.setFillColor(colors.HexColor("#1e293b"))
                pdf.circle(due_x, y + 7, 1.6, stroke=0, fill=1)
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def _paragraph(text, style):
    if not text:
        return Paragraph("—", style)
    safe = html.escape(str(text)).replace("\n", "<br/>")
    return Paragraph(safe, style)


def mom_pdf(project, mom) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"MOM - {project.project_code}",
        author="SORA ERP",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5,
        leading=14, textColor=colors.HexColor("#334155"), spaceAfter=6,
    )
    heading = ParagraphStyle(
        "Heading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11,
        leading=14, textColor=colors.HexColor("#1e293b"), spaceBefore=10, spaceAfter=6,
    )
    title = ParagraphStyle(
        "Title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=19,
        leading=23, textColor=colors.HexColor("#1e293b"), alignment=0, spaceAfter=4,
    )
    small = ParagraphStyle(
        "Small", parent=body, fontSize=8, textColor=colors.HexColor("#64748b"),
    )

    story = [
        Paragraph("SORA ERP · MINUTES OF MEETING", ParagraphStyle(
            "Kicker", parent=small, fontName="Helvetica-Bold", textColor=GOLD, fontSize=8,
        )),
        Paragraph(html.escape(mom.title), title),
        Paragraph(
            f"{html.escape(project.project_code)} · {html.escape(project.project_name)}",
            ParagraphStyle("Project", parent=small, fontSize=10, textColor=SLATE),
        ),
        Spacer(1, 8),
    ]

    meta = [
        ["Meeting date", mom.meeting_date.strftime("%d %B %Y")],
        ["Status", mom.get_status_display()],
        ["Attendees", mom.attendees or "—"],
        ["Client", project.client_name or "—"],
        ["Prepared by", f"SORA ERP · {timezone.localtime(mom.created_at):%d %b %Y}"
         if mom.created_at else "SORA ERP"],
    ]
    table = Table(meta, colWidths=[36 * mm, 120 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PAPER),
        ("TEXTCOLOR", (0, 0), (0, -1), SLATE),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 8)]

    sections = [
        ("Agenda", mom.agenda),
        ("Minutes", mom.minutes),
        ("Decisions", mom.decisions),
        ("Action items", mom.action_items),
    ]
    for label, value in sections:
        story.append(Paragraph(label, heading))
        story.append(_paragraph(value, body))

    decision = "Approved" if mom.status == "approved" else (
        "Rejected" if mom.status == "rejected" else "Pending client approval"
    )
    approval_rows = [
        ["Client decision", decision],
        ["Approved / rejected by", mom.approved_by_name or "—"],
        [
            "Decision date",
            timezone.localtime(mom.approved_at).strftime("%d %B %Y %H:%M")
            if mom.approved_at else "—",
        ],
    ]
    approval = Table(approval_rows, colWidths=[46 * mm, 110 * mm])
    approval.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PAPER),
        ("TEXTCOLOR", (0, 0), (0, -1), SLATE),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [Spacer(1, 6), Paragraph("Client approval record", heading), approval]
    if mom.rejection_reason:
        story += [Paragraph("Client comments", heading), _paragraph(mom.rejection_reason, body)]
    story += [
        Spacer(1, 14),
        Paragraph(
            "This document was issued through the SORA ERP MOM Shield. Client approval is captured "
            "via a single-use, HMAC-signed magic link and recorded against this document.",
            small,
        ),
    ]

    def footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setStrokeColor(LIGHT)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        canvas_obj.setFillColor(SLATE)
        canvas_obj.setFont("Helvetica", 7)
        canvas_obj.drawString(18 * mm, 10 * mm, f"{project.project_code} · Minutes of Meeting")
        canvas_obj.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc_obj.page} · SORA ERP")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def report_pdf(title, subtitle, headers, rows, filters_text="") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title=title, author="SORA ERP",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=17, leading=21, alignment=0, textColor=NAVY,
    )
    small = ParagraphStyle(
        "ReportSmall", parent=styles["BodyText"], fontSize=8, textColor=SLATE,
    )
    story = [
        Paragraph("SORA ERP · PROJECT ANALYTICS", ParagraphStyle(
            "ReportKicker", parent=small, fontName="Helvetica-Bold", textColor=GOLD,
        )),
        Paragraph(html.escape(title), title_style),
        Paragraph(html.escape(subtitle), small),
        Spacer(1, 8),
    ]
    if filters_text:
        story.append(Paragraph(html.escape(filters_text), small))
        story.append(Spacer(1, 6))
    table_data = [[Paragraph(f"<b>{html.escape(str(header))}</b>", small) for header in headers]]
    for row in rows[:400]:
        table_data.append([
            Paragraph(html.escape(str(value if value is not None else "")), small)
            for value in row
        ])
    table = Table(table_data, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)
    if len(rows) > 400:
        story.append(Paragraph(f"Showing the first 400 of {len(rows)} rows.", small))
    story += [
        Spacer(1, 8),
        Paragraph(f"Generated {timezone.localtime():%d %b %Y %H:%M} · SORA ERP", small),
    ]

    def footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFillColor(SLATE)
        canvas_obj.setFont("Helvetica", 7)
        canvas_obj.drawRightString(
            landscape(A4)[0] - 14 * mm, 7 * mm, f"Page {doc_obj.page} · SORA ERP"
        )
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
