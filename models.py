from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class SubmissionRequest(BaseModel):
    team_name: str
    project_title: str
    member_names: str
    contact_email: str
    track: str
    problem_statement: str
    github_url: str
    gemini_api_key: str
    project_description: Optional[str] = ""

class ScoreDetail(BaseModel):
    score: float
    reasoning: str

class JudgingResult(BaseModel):
    inferred_purpose: str
    confidence: str
    analysis_mode: str
    scores: Dict[str, Any]
    total_score: float
    overall_feedback: str
    strengths: List[str]
    weaknesses: List[str]
