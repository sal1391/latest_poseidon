"""
Perplexity JSON Schema for Operational Profile (Contextualizer - New Accounts).
Returns structured voyage info, vessel types, IMO numbers, and preferred ports.
"""
from datetime import datetime


def get_schema(company_name):
    """Return the Perplexity response_format dict for OperationalProfileReport."""
    default = "Couldn't find information"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "OperationalProfileReport",
            "schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "OperationalProfileReport",
                "type": "object",
                "description": (
                    "Maritime operational profile for a company, including "
                    "voyage information, vessel fleet details, IMO numbers, "
                    "and preferred ports."
                ),
                "required": [
                    "company",
                    "voyage_information",
                    "vessel_types",
                    "vessel_imo_numbers",
                    "preferred_ports",
                    "notes"
                ],
                "properties": {
                    "company": {
                        "type": "string",
                        "const": company_name,
                        "description": "The name of the target company."
                    },
                    "report_run_date": {
                        "type": "string",
                        "format": "date",
                        "const": datetime.today().date().isoformat(),
                        "description": "The date this report was generated."
                    },
                    "org": {
                        "type": "string",
                        "const": "Maritime",
                        "description": "The overarching organization or sector context for this report."
                    },
                    "voyage_information": {
                        "type": "object",
                        "description": "Overview of voyage operations.",
                        "required": [
                            "operational_overview",
                            "route_types",
                            "voyage_frequency",
                            "operational_patterns",
                            "ports"
                        ],
                        "properties": {
                            "operational_overview": {
                                "type": "string",
                                "default": default,
                                "description": (
                                    "General description of voyage operations "
                                    "(e.g., container shipping, dry bulk, tanker, ferry)."
                                )
                            },
                            "route_types": {
                                "type": "string",
                                "default": default,
                                "description": (
                                    "Types of routes (coastal, deep-sea, "
                                    "transoceanic, regional, etc.)."
                                )
                            },
                            "voyage_frequency": {
                                "type": "string",
                                "default": default,
                                "description": (
                                    "Approximate voyage frequency or volume "
                                    "(e.g., regular liner schedule, hundreds per month)."
                                )
                            },
                            "operational_patterns": {
                                "type": "string",
                                "default": default,
                                "description": (
                                    "Notable operational patterns such as seasonal "
                                    "peaks, regional hubs, point-to-point, 24/7 ops."
                                )
                            },
                            "ports": {
                                "type": "array",
                                "default": default,
                                "description": (
                                    "Notable ports such as seasonal and home base terminals."
                                )
                            }
                        }
                    },
                    "vessel_types": {
                        "type": "array",
                        "description": "Vessel types operated or managed by this company.",
                        "items": {
                            "type": "object",
                            "required": ["type_name", "category", "typical_use"],
                            "properties": {
                                "type_name": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "Specific vessel class (e.g., Panamax, "
                                        "Suezmax, VLCC, Capesize)."
                                    )
                                },
                                "category": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "Vessel category (e.g., container ship, bulk carrier, "
                                        "oil tanker, ferry)."
                                    )
                                },
                                "typical_use": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "Typical use case for this vessel type "
                                        "(e.g., long-range international, short-haul coastal)."
                                    )
                                }
                            }
                        }
                    },
                    "vessel_imo_numbers": {
                        "type": "array",
                        "description": (
                            "Known IMO numbers registered under this company."
                        ),
                        "items": {
                            "type": "object",
                            "required": [
                                "imo_number",
                                "vessel_type",
                                "registration_status"
                            ],
                            "properties": {
                                "imo_number": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "IMO registration number "
                                        "(e.g., IMO 9123456)."
                                    )
                                },
                                "vessel_type": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "Vessel type associated with this IMO number."
                                    )
                                },
                                "registration_status": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "Registration status (active, decommissioned, laid up)."
                                    )
                                }
                            }
                        }
                    },
                    "preferred_ports": {
                        "type": "array",
                        "description": (
                            "Ports frequently used or preferred by this company."
                        ),
                        "items": {
                            "type": "object",
                            "required": [
                                "unlocode",
                                "port_name",
                                "usage_context"
                            ],
                            "properties": {
                                "unlocode": {
                                    "type": "string",
                                    "default": default,
                                    "description": "UN/LOCODE port code (e.g., NLRTM, SGSIN)."
                                },
                                "port_name": {
                                    "type": "string",
                                    "default": default,
                                    "description": "Full port name."
                                },
                                "usage_context": {
                                    "type": "string",
                                    "default": default,
                                    "description": (
                                        "How this port is used (e.g., home base, "
                                        "frequent destination, maintenance hub, terminal partner)."
                                    )
                                }
                            }
                        }
                    },
                    "notes": {
                        "type": "object",
                        "description": "Analytical notes and data gaps.",
                        "required": ["inferred_points", "missing_data"],
                        "properties": {
                            "inferred_points": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Strategic interpretations or logical deductions."
                                )
                            },
                            "missing_data": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Data that could not be found during research."
                                )
                            }
                        }
                    }
                }
            }
        }
    }
