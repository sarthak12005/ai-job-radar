import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from pypdf import PdfReader
import config

class ResumeParser:
    def __init__(self, resume_path=None):
        self.resume_path = Path(resume_path or config.RESUME_PATH)
        self.profile = {
            "name": config.CANDIDATE_NAME,
            "email": config.CANDIDATE_EMAIL,
            "phone": config.CANDIDATE_PHONE,
            "title": "Full Stack / Backend Developer",
            "experience_years": 1.8,
            "core_skills": config.CORE_SKILLS,
            "raw_text": "",
            "summary": "Full Stack & Backend Developer with 1.8+ years of production experience in Node.js, NestJS, TypeScript, PostgreSQL, MongoDB, Redis, and REST APIs."
        }
        self.load_resume()

    def load_resume(self):
        """Attempts to read from PDF or DOCX resume, otherwise uses verified defaults."""
        raw_text = ""
        # Check primary path or alternative files in parent folder
        candidates = [
            self.resume_path,
            config.BASE_DIR / "sarthak_joshi_fullstack_developer_nov_2026.pdf",
            config.BASE_DIR / "resume.pdf",
            config.BASE_DIR.parent / "sarthak_joshi_backend_oct_2026.pdf",
            config.BASE_DIR.parent / "Sarthak_Joshi_FullStack_Oct_2026.pdf",
            config.BASE_DIR.parent / "Sarthak_resume_doc_copy.docx"
        ]

        for p in candidates:
            if p.exists():
                try:
                    if p.suffix.lower() == ".pdf":
                        reader = PdfReader(str(p))
                        raw_text = "\n".join([page.extract_text() or "" for page in reader.pages])
                    elif p.suffix.lower() == ".docx":
                        with zipfile.ZipFile(str(p)) as z:
                            xml_content = z.read("word/document.xml")
                            tree = ET.fromstring(xml_content)
                            raw_text = " ".join([node.text for node in tree.iter() if node.text])
                    if raw_text.strip():
                        self.resume_path = p
                        self.profile["raw_text"] = raw_text
                        break
                except Exception as e:
                    print(f"Notice: Could not parse {p.name}: {e}")

        # Extract or refine skills present in resume text
        if raw_text:
            found_skills = set()
            for skill in config.CORE_SKILLS:
                pattern = r'\b' + re.escape(skill) + r'\b'
                if re.search(pattern, raw_text, re.IGNORECASE):
                    found_skills.add(skill)
            if found_skills:
                self.profile["core_skills"] = list(found_skills)

    def get_profile(self):
        return self.profile

if __name__ == "__main__":
    parser = ResumeParser()
    prof = parser.get_profile()
    print("Parsed Candidate:", prof["name"])
    print("Email:", prof["email"])
    print("Skills Count:", len(prof["core_skills"]))
    print("Skills:", ", ".join(prof["core_skills"]))

