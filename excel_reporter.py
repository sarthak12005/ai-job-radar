import datetime
from pathlib import Path
from typing import List, Dict, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

class ExcelReporter:
    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or (Path(__file__).resolve().parent / "reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, jobs: List[Dict[str, Any]], candidate_name="Sarthak Joshi") -> Path:
        """
        Builds a styled Excel spreadsheet containing all matched jobs,
        direct career links, HR emails, and outreach copy.
        """
        # Sort jobs by match score descending
        sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score", 0), reverse=True)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        file_path = self.output_dir / f"Jobs_Matched_{candidate_name.replace(' ', '_')}_{today_str}.xlsx"

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Today's Job Matches"
        ws.views.sheetView[0].showGridLines = True

        # Styles definition
        title_font = Font(name="Calibri", size=16, bold=True, color="1F4E79")
        subtitle_font = Font(name="Calibri", size=11, italic=True, color="595959")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        
        green_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # High match
        blue_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")  # Good match
        zebra_fill = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3")
        )

        # 1. Header Banner
        ws.merge_cells("A1:K1")
        ws["A1"] = f"🎯 Daily Job Match Briefing for {candidate_name}"
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(vertical="center")

        ws.merge_cells("A2:K2")
        ws["A2"] = f"Generated on: {today_str} | Target: Backend & Full Stack (Node.js, NestJS, TypeScript, PostgreSQL, Redis) | 1.8+ yrs Experience"
        ws["A2"].font = subtitle_font
        ws["A2"].alignment = Alignment(vertical="center")

        # 2. KPI Summary Bar (Row 4)
        top_matches = len([j for j in sorted_jobs if j.get("match_score", 0) >= 70])
        ws["A4"] = "Total Jobs Found"
        ws["B4"] = len(sorted_jobs)
        ws["C4"] = "High Matches (>=70%)"
        ws["D4"] = top_matches
        ws["E4"] = "Direct Apply Links"
        ws["F4"] = f"{len([j for j in sorted_jobs if j.get('url')])} Ready"

        for col_idx in ["A4", "C4", "E4"]:
            ws[col_idx].font = Font(bold=True, color="1F4E79")
            ws[col_idx].fill = blue_fill
        for col_idx in ["B4", "D4", "F4"]:
            ws[col_idx].font = Font(bold=True)
            ws[col_idx].alignment = Alignment(horizontal="center")

        # 3. Table Column Headers (Row 6)
        headers = [
            ("Match %", 10),
            ("Job Title", 32),
            ("Company", 22),
            ("Location / Mode", 18),
            ("Matching Stack & Analysis", 36),
            ("Direct Apply / Career URL", 30),
            ("HR / Recruiter Email", 30),
            ("Contact Channel", 24),
            ("Cold Outreach Subject", 35),
            ("Cold Outreach Message", 45),
            ("Date / Source", 18)
        ]

        header_row = 6
        for col_num, (h_title, _) in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_num)
            cell.value = h_title
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
        ws.row_dimensions[header_row].height = 28

        # 4. Fill Data Rows
        current_row = header_row + 1
        link_font = Font(name="Calibri", size=10, color="0563C1", underline="single")
        regular_font = Font(name="Calibri", size=10)
        bold_font = Font(name="Calibri", size=10, bold=True)

        for job in sorted_jobs:
            score = job.get("match_score", 0)
            url = job.get("url", "")
            hr_email = job.get("contact_email", "")

            # Row fill
            row_fill = green_fill if score >= 75 else (blue_fill if score >= 60 else (zebra_fill if current_row % 2 == 0 else white_fill))

            # Col 1: Match Score
            c1 = ws.cell(row=current_row, column=1, value=f"{score}%")
            c1.font = bold_font
            c1.alignment = Alignment(horizontal="center", vertical="center")
            c1.fill = row_fill
            c1.border = thin_border

            # Col 2: Title
            c2 = ws.cell(row=current_row, column=2, value=job.get("title", ""))
            c2.font = bold_font
            c2.alignment = Alignment(vertical="center")
            c2.border = thin_border

            # Col 3: Company
            c3 = ws.cell(row=current_row, column=3, value=job.get("company", ""))
            c3.font = regular_font
            c3.alignment = Alignment(vertical="center")
            c3.border = thin_border

            # Col 4: Location
            c4 = ws.cell(row=current_row, column=4, value=job.get("location", "Remote"))
            c4.font = regular_font
            c4.alignment = Alignment(horizontal="center", vertical="center")
            c4.border = thin_border

            # Col 5: Match Analysis
            c5 = ws.cell(row=current_row, column=5, value=job.get("match_summary", ""))
            c5.font = regular_font
            c5.alignment = Alignment(vertical="center")
            c5.border = thin_border

            # Col 6: Direct Apply Link (Hyperlinked)
            c6 = ws.cell(row=current_row, column=6)
            if url:
                c6.value = "👉 Click to Apply"
                c6.hyperlink = url
                c6.font = link_font
            else:
                c6.value = "N/A"
                c6.font = regular_font
            c6.alignment = Alignment(horizontal="center", vertical="center")
            c6.border = thin_border

            # Col 7: HR Email (mailto hyperlink)
            c7 = ws.cell(row=current_row, column=7)
            if hr_email:
                c7.value = hr_email
                c7.hyperlink = f"mailto:{hr_email}?subject={job.get('cold_outreach_subject', '')}"
                c7.font = link_font
            else:
                c7.value = "careers@company.com"
                c7.font = regular_font
            c7.alignment = Alignment(vertical="center")
            c7.border = thin_border

            # Col 8: Contact Channel
            c8 = ws.cell(row=current_row, column=8, value=job.get("contact_type", "Careers Desk"))
            c8.font = regular_font
            c8.alignment = Alignment(vertical="center")
            c8.border = thin_border

            # Col 9: Cold Outreach Subject
            c9 = ws.cell(row=current_row, column=9, value=job.get("cold_outreach_subject", ""))
            c9.font = regular_font
            c9.alignment = Alignment(vertical="center")
            c9.border = thin_border

            # Col 10: Cold Outreach Message
            c10 = ws.cell(row=current_row, column=10, value=job.get("cold_outreach_body", ""))
            c10.font = regular_font
            c10.alignment = Alignment(vertical="top", wrap_text=True)
            c10.border = thin_border

            # Col 11: Date / Source
            c11 = ws.cell(row=current_row, column=11, value=f"{job.get('date_posted', '')} ({job.get('source', '')})")
            c11.font = regular_font
            c11.alignment = Alignment(horizontal="center", vertical="center")
            c11.border = thin_border

            ws.row_dimensions[current_row].height = 40
            current_row += 1

        # 5. Set Column Widths
        for idx, (_, width) in enumerate(headers, 1):
            col_letter = get_column_letter(idx)
            ws.column_dimensions[col_letter].width = width

        wb.save(file_path)
        return file_path

