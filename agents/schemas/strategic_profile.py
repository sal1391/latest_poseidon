"""
Perplexity JSON Schema for Strategic Profile Report.
Source: agent1.ipynb cell 8
"""
from datetime import datetime


def get_schema(company_name):
    """Return the Perplexity response_format dict for StrategicProfileReport."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "StrategicProfileReport",
            "schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "StrategicProfileReport",
                "description": "A comprehensive strategic profile report for a company.",
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "company",
                    "report_run_date",
                    "org",
                    "strategic_profile",
                    "notes"
                ],
                "properties": {
                    "company": {
                        "type": "string",
                        "const": company_name,
                        "description": "The exact name of the company being profiled."
                    },
                    "report_run_date": {
                        "type": "string",
                        "format": "date",
                        "const": datetime.today().date().isoformat(),
                        "description": "The date the report was generated."
                    },
                    "org": {
                        "type": "string",
                        "const": "Maritime",
                    },
                    "strategic_profile": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["business_model", "financials", "market_presence"],
                        "properties": {
                            "business_model": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["value_propositions", "key_products", "target_audience"],
                                "properties": {
                                    "value_propositions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "minItems": 1
                                    },
                                    "key_products": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "minItems": 1
                                    },
                                    "target_audience": {
                                        "type": "string",
                                        "description": "Primary target market or customer segment."
                                    }
                                }
                            },
                            "financials": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["revenue_model", "growth_status", "growth_rationale"],
                                "properties": {
                                    "revenue_model": {
                                        "type": "string",
                                        "description": "How the company generates revenue."
                                    },
                                    "growth_status": {
                                        "type": "string",
                                        "enum": ["expanding", "stable", "contracting"],
                                        "description": "Current growth trajectory."
                                    },
                                    "growth_rationale": {
                                        "type": "string",
                                        "description": "Evidence or reasoning behind the growth status."
                                    },
                                    "previous_year_revenue": {"type": "string"},
                                    "current_year_revenue": {"type": "string"},
                                    "projected_next_year_revenue": {"type": "string"},
                                    "growth_percentage": {"type": "string"}
                                }
                            },
                            "market_presence": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["regions", "primary_markets"],
                                "properties": {
                                    "regions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Geographic regions of operation."
                                    },
                                    "primary_markets": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Specific countries or markets served."
                                    },
                                    "competitors": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Primary competitors in these markets."
                                    }
                                }
                            }
                        }
                    },
                    "notes": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["inferred_points", "missing_data"],
                        "properties": {
                            "inferred_points": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "missing_data": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
