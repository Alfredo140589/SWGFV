from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from django.contrib.staticfiles import finders


def build_fortia_doc(response, title: str, author: str = "SWGFV"):
    return SimpleDocTemplate(
        response,
        pagesize=letter,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=3.6 * cm,
        bottomMargin=2.0 * cm,
        title=title,
        author=author,
    )


def get_fortia_styles():
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "FortiaTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0B2E59"),
        spaceAfter=5,
        alignment=TA_JUSTIFY,
    )

    subtitle_style = ParagraphStyle(
        "FortiaSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#555555"),
        spaceAfter=3,
        alignment=TA_CENTER,
    )

    section_style = ParagraphStyle(
        "FortiaSection",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=colors.white,
        backColor=colors.HexColor("#0B2E59"),
        borderPadding=(4, 4, 4),
        spaceBefore=8,
        spaceAfter=8,
        alignment=TA_CENTER,
    )

    label_style = ParagraphStyle(
        "FortiaLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        textColor=colors.HexColor("#0B2E59"),
        leading=10,
    )

    value_style = ParagraphStyle(
        "FortiaValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        textColor=colors.HexColor("#222222"),
        leading=10,
        wordWrap="CJK",
    )

    small_style = ParagraphStyle(
        "FortiaSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
    )

    block_title_style = ParagraphStyle(
        "FortiaBlockTitle",
        parent=styles["Heading4"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        textColor=colors.HexColor("#0B2E59"),
        spaceAfter=6,
        spaceBefore=4,
        alignment=TA_JUSTIFY,
    )

    wrap_style = ParagraphStyle(
        "FortiaWrap",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.2,
        leading=9.5,
        textColor=colors.HexColor("#222222"),
        wordWrap="CJK",
    )

    return {
        "title": title_style,
        "subtitle": subtitle_style,
        "section": section_style,
        "label": label_style,
        "value": value_style,
        "small": small_style,
        "block_title": block_title_style,
        "wrap": wrap_style,
    }


def draw_fortia_letterhead(canvas, doc):
    width, height = letter

    bg_path = finders.find("core/img/hoja_membretada.png")
    if bg_path:
        try:
            canvas.drawImage(
                bg_path,
                0,
                0,
                width=width,
                height=height,
                preserveAspectRatio=False,
                mask="auto",
            )
        except Exception:
            pass


def add_fortia_header(elements, title: str, subtitle: str, styles_dict: dict):
    elements.append(Spacer(1, 0.4 * cm))
    elements.append(Paragraph(title, styles_dict["title"]))
    if subtitle:
        elements.append(Paragraph(subtitle, styles_dict["subtitle"]))
    elements.append(Spacer(1, 0.15 * cm))


def make_info_table(data, col_widths):
    table = Table(data, colWidths=col_widths, repeatRows=0)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.Color(1, 1, 1, alpha=0.90)),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#C9D3E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def make_data_table(data, col_widths, header_bg="#0B2E59"):
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.2),
        ("BACKGROUND", (0, 1), (-1, -1), colors.Color(1, 1, 1, alpha=0.92)),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#C9D3E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.Color(1, 1, 1, alpha=0.96), colors.HexColor("#F8FBFF")]),
    ]))
    return table


def add_fortia_footer(elements, styles_dict: dict):
    elements.append(Spacer(1, 0.25 * cm))