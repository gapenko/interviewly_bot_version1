"""
docx_service.py — генерация форматированного Word-документа (.docx).
"""
import html
import io
import re
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH


def create_candidate_docx(username: str, report_text: str, resume_draft: str, answers: list[dict]) -> io.BytesIO:
    """Создает документ с аудитом, резюме и ответами кандидата."""
    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title_run = title.add_run(f"ОТЧЕТ ПО АССЕССМЕНТУ: @{username or 'Candidate'}")
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(18)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

    sub = doc.add_paragraph()
    sub_run = sub.add_run("Результаты технического AI-интервью и проектный черновик резюме")
    sub_run.font.name = "Calibri"
    sub_run.font.size = Pt(10)
    sub_run.font.italic = True
    sub_run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    doc.add_paragraph("―" * 50)

    def clean_html(text: str) -> str:
        # Убираем любые HTML-теги Telegram и раскодируем сущности (&lt; &gt; &amp; и т.д.)
        text = re.sub(r"</?(b|i|u|s|code|pre|blockquote)>", "", str(text or ""))
        return html.unescape(text).strip()

    # 1. Отчет
    h1 = doc.add_paragraph()
    h1_run = h1.add_run("1. ИТОГОВЫЙ АУДИТ НАВЫКОВ (HARD & SOFT SKILLS)")
    h1_run.font.name = "Calibri"
    h1_run.font.size = Pt(13)
    h1_run.font.bold = True
    h1_run.font.color.rgb = RGBColor(0x0F, 0x24, 0x3E)

    doc.add_paragraph(clean_html(report_text))
    doc.add_paragraph("―" * 50)

    # 2. Резюме
    h2 = doc.add_paragraph()
    h2_run = h2.add_run("2. ЧЕРНОВИК РЕЗЮМЕ ДЛЯ HEADHUNTER / LINKEDIN")
    h2_run.font.name = "Calibri"
    h2_run.font.size = Pt(13)
    h2_run.font.bold = True
    h2_run.font.color.rgb = RGBColor(0x0F, 0x24, 0x3E)

    doc.add_paragraph(clean_html(resume_draft))
    doc.add_paragraph("―" * 50)

    # 3. Ответы
    h3 = doc.add_paragraph()
    h3_run = h3.add_run("3. СТЕНОГРАММА ИНТЕРВЬЮ")
    h3_run.font.name = "Calibri"
    h3_run.font.size = Pt(13)
    h3_run.font.bold = True
    h3_run.font.color.rgb = RGBColor(0x0F, 0x24, 0x3E)

    for idx, item in enumerate(answers, 1):
        p_q = doc.add_paragraph()
        q_run = p_q.add_run(f"Вопрос {idx}: {clean_html(item.get('question_text', ''))}")
        q_run.bold = True
        q_run.font.name = "Calibri"
        q_run.font.size = Pt(10.5)

        p_a = doc.add_paragraph()
        a_run = p_a.add_run(f"Ответ кандидата: {clean_html(item.get('answer', ''))}")
        a_run.font.name = "Calibri"
        a_run.font.size = Pt(10)

        if item.get("feedback"):
            p_f = doc.add_paragraph()
            score = item.get("score")
            score_note = f" (оценка: {score}/10)" if score else ""
            f_run = p_f.add_run(f"Комментарий ментора{score_note}:\n{clean_html(item.get('feedback', ''))}")
            f_run.italic = True
            f_run.font.name = "Calibri"
            f_run.font.size = Pt(9.5)
            f_run.font.color.rgb = RGBColor(0x40, 0x40, 0x40)

        doc.add_paragraph("")

    stream = io.BytesIO()
    doc.save(stream)
    stream.seek(0)
    return stream