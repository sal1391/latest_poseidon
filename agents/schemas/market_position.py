"""
Perplexity JSON Schema for Market Position Report.
Source: agent1.ipynb cell 4
"""
from datetime import datetime


def get_schema(company_name):
    """Return the Perplexity response_format dict for MarketPositionReport."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "MarketPositionReport",
            "schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "MarketPositionReport",
                "type": "object",
                "description": "A comprehensive market research report analyzing a company's corporate identity, industry standing, and competitive landscape.",
                "required": [
                    "company",
                    "industry_classification",
                    "market_position",
                    "competitive_landscape",
                    "notes"
                ],
                "properties": {
                    "company": {
                        "type": "string",
                        "const": company_name,
                        "description": "The name of the target company being analyzed."
                    },
                    "report_run_date": {
                        "type": "string",
                        "format": "date",
                        "const": datetime.today().date().isoformat(),
                        "description": "The date this market research report was generated."
                    },
                    "org": {
                        "type": "string",
                        "const": "Maritime",
                        "description": "The overarching organization or sector context for this report."
                    },
                    "vision": {
                        "type": "object",
                        "description": "The company's long-term vision.",
                        "required": ["statement"],
                        "properties": {
                            "statement": {"type": "string", "description": "The official or inferred forward-looking vision statement."}
                        }
                    },
                    "mission": {
                        "type": "object",
                        "description": "The company's current mission.",
                        "required": ["statement"],
                        "properties": {
                            "statement": {"type": "string", "description": "The official or inferred mission statement."}
                        }
                    },
                    "industry_classification": {
                        "type": "object",
                        "description": "Categorization of the company within the broader economic and market landscape.",
                        "required": ["sector", "primary_business"],
                        "properties": {
                            "sector": {"type": "string", "description": "The broad economic sector."},
                            "primary_business": {"type": "string", "description": "The specific primary business activity."}
                        }
                    },
                    "market_position": {
                        "type": "object",
                        "description": "Evaluation of the company's standing within its industry.",
                        "required": ["role", "approximate_standing"],
                        "properties": {
                            "role": {"type": "string", "description": "Strategic role (e.g., Market Leader, Disruptor, Challenger, Niche Player)."},
                            "approximate_standing": {"type": "string", "description": "Approximate market share or ranking description."}
                        }
                    },
                    "competitive_landscape": {
                        "type": "object",
                        "description": "Analysis of the competitive environment.",
                        "required": ["main_competitors", "differentiation"],
                        "properties": {
                            "main_competitors": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of main competitors."
                            },
                            "differentiation": {"type": "string", "description": "How the company differentiates from competitors."}
                        }
                    },
                    "notes": {
                        "type": "object",
                        "description": "Analytical notes and metadata.",
                        "required": ["inferred_points", "unknowns"],
                        "properties": {
                            "inferred_points": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Strategic interpretations or deductions."
                            },
                            "unknowns": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Data points that could not be verified."
                            }
                        }
                    }
                }
            }
        }
    }
