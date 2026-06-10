from datetime import datetime

def get_schema(company_name):
    """Return the Perplexity response_format dict for LatestNewsPartnerships."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "LatestNewsPartnershipsReport",
            "schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "LatestNewsPartnershipsReport",
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "company_name",
                    "report_as_date",
                    "org",
                    "recent_news",
                    "strategic_partnerships",
                    "notes",
                    "sources"
                ],
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": "The name of the company being analyzed"
                    },
                    "report_as_date": {
                        "type": "string",
                        "format": "date",
                        "description": "The date the report is generated or the 'as of' date"
                    },
                    "org": {
                        "type": "string"
                    },
                    "recent_news": {
                        "type": "array",
                        "description": "Recent corporate news and announcements",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "date", "summary"],
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Headline or title of the news item"
                                },
                                "date": {
                                    "type": "string",
                                    "description": "Date of the announcement"
                                },
                                "summary": {
                                    "type": "string",
                                    "description": "Brief summary of the news and its relevance"
                                }
                            }
                        }
                    },
                    "strategic_partnerships": {
                        "type": "array",
                        "description": "Recent strategic partnerships, M&A, or joint ventures",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["partner_name", "date", "purpose", "impact"],
                            "properties": {
                                "partner_name": {
                                    "type": "string",
                                    "description": "Name of the partner organization"
                                },
                                "date": {
                                    "type": "string",
                                    "description": "Date the partnership was announced or formed"
                                },
                                "purpose": {
                                    "type": "string",
                                    "description": "The stated goal or purpose of the partnership"
                                },
                                "impact": {
                                    "type": "string",
                                    "description": "Expected strategic or operational impact"
                                }
                            }
                        }
                    },
                    "notes": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["unknowns", "inferred_points"],
                        "properties": {
                            "unknowns": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "inferred_points": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        }
    }
